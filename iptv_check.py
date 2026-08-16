"""
IPTV 直播源校验工具（用 curl 当抓取后端，规避无 _ssl 的 python）
用法: python3 iptv_check.py <输入文件> [并发数]
输入格式每行: 名称,URL
判定: SPEED_TIME 秒内下载到 >= MIN_BYTES 且 HTTP 2xx 判为“能用”，记录平均速度
      m3u8 会解析切片后再测真实切片速度
减少误杀:
  1) 阈值放宽(32KB / 8s)，给跨国慢但可用的源留余地
  2) 首测失败自动重试一次，换播放器 UA(okhttp) + 自动 Referer，
     救“认播放器UA / 防盗链”的源(普通 UA 被挡 403/404 的)
输出: 有效源按速度从快到慢排序 + 写 *_valid.txt
"""
import sys, time, subprocess, tempfile, os, urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed

DETECT_TIME = 5
SPEED_TIME = 8
MIN_BYTES = 32 * 1024
UA = "Mozilla/5.0 (Linux; Android 12) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36"
PLAYER_UA = "okhttp/3.12.13"     # 很多 IPTV/点播源只认播放器 UA，普通浏览器 UA 被挡


def _referer(url):
    """从 URL 取 scheme://host/ 作 Referer，绕常见防盗链。"""
    try:
        p = urllib.parse.urlparse(url)
        if p.scheme and p.netloc:
            return "%s://%s/" % (p.scheme, p.netloc)
    except Exception:
        pass
    return ""


def curl_body(url, out_path, max_time=DETECT_TIME, rng="0-16383", ua=UA, referer=""):
    """抓 body 到 out_path，返回 (http_code, effective_url)"""
    cmd = ["curl", "-sL", "--max-time", str(max_time), "-A", ua,
           "-H", "Connection: close"]
    if referer:
        cmd += ["-e", referer]
    cmd += ["--range", rng, "-o", out_path,
            "-w", "%{http_code}\t%{url_effective}", url]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=max_time + 4)
        parts = (p.stdout or "\t").split("\t")
        return parts[0], (parts[1] if len(parts) > 1 else url)
    except Exception:
        return "000", url


def curl_speed(url, max_time=SPEED_TIME, ua=UA, referer=""):
    """测速：返回 (speed_bytes_per_s, http_code, size_download)"""
    cmd = ["curl", "-sL", "--max-time", str(max_time), "-A", ua,
           "-H", "Connection: close"]
    if referer:
        cmd += ["-e", referer]
    cmd += ["-o", os.devnull,
            "-w", "%{speed_download}\t%{http_code}\t%{size_download}", url]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=max_time + 4)
        a = (p.stdout or "0\t000\t0").split("\t")
        return float(a[0] or 0), a[1], int(float(a[2] or 0))
    except Exception:
        return 0.0, "000", 0


def pick_segment(text, base):
    for l in text.splitlines():
        l = l.strip()
        if l and not l.startswith("#"):
            return urllib.parse.urljoin(base, l)
    return None


def _attempt(url, ua, referer):
    """一次完整检测（指定 UA/Referer）。返回 dict(ok, reason, kbps)。"""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".bin").name
    try:
        code, eff = curl_body(url, tmp, ua=ua, referer=referer)
        with open(tmp, "rb") as f:
            head = f.read(65536)
        is_m3u8 = head[:7] == b"#EXTM3U" or url.split("?")[0].lower().endswith(".m3u8")

        if is_m3u8:
            seg = pick_segment(head.decode("utf-8", "ignore"), eff)
            if not seg:
                return dict(ok=False, reason="m3u8无切片", kbps=0)
            if seg.split("?")[0].lower().endswith(".m3u8"):
                code2, eff2 = curl_body(seg, tmp, rng="0-65535", ua=ua, referer=referer)
                with open(tmp, "rb") as f:
                    sub = f.read(65536)
                seg = pick_segment(sub.decode("utf-8", "ignore"), eff2)
                if not seg:
                    return dict(ok=False, reason="子m3u8无切片", kbps=0)
            spd, code, size = curl_speed(seg, ua=ua, referer=referer)
        else:
            spd, code, size = curl_speed(url, ua=ua, referer=referer)

        kbps = round(spd / 1024.0, 1)
        ok = size >= MIN_BYTES and code.startswith("2")
        reason = "ok" if ok else (f"HTTP {code}" if not code.startswith("2") else f"数据不足({size}B)")
        return dict(ok=ok, reason=reason, kbps=kbps)
    except Exception as e:
        return dict(ok=False, reason=f"err:{type(e).__name__}", kbps=0)
    finally:
        try:
            os.unlink(tmp)
        except Exception:
            pass


def check(name, url):
    t0 = time.time()
    r = _attempt(url, UA, "")                       # 第一次：普通浏览器 UA
    if not r["ok"]:
        r2 = _attempt(url, PLAYER_UA, _referer(url))  # 第二次：播放器 UA + Referer，救防盗链/认UA的源
        if r2["ok"]:
            r2["reason"] = "ok(播放器UA)"
            r = r2
    r.update(name=name, url=url, ttfb=round(time.time() - t0, 2))
    return r


def parse_file(path):
    items = []
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            name, url = (line.split(",", 1) + [""])[:2] if "," in line else ("", line)
            url = url.strip()
            if url.startswith("http"):
                items.append((name.strip(), url))
    return items


def main():
    if len(sys.argv) < 2:
        print("用法: python3 iptv_check.py <输入文件> [并发数]"); sys.exit(1)
    path = sys.argv[1]
    workers = int(sys.argv[2]) if len(sys.argv) > 2 else 16
    items = parse_file(path)
    print(f"共 {len(items)} 条，并发 {workers}，开始检测...\n", flush=True)

    results = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(check, n, u): (n, u) for n, u in items}
        done = 0
        for fut in as_completed(futs):
            r = fut.result(); results.append(r); done += 1
            flag = "OK " if r["ok"] else "X  "
            print(f"[{done}/{len(items)}] {flag} {r['kbps']:>7} KB/s  {r['name']}  {r['reason']}", flush=True)

    ok = sorted([r for r in results if r["ok"]], key=lambda x: -x["kbps"])
    bad = [r for r in results if not r["ok"]]
    print("\n" + "=" * 70)
    print(f"有效 {len(ok)} 条 / 失效 {len(bad)} 条\n")
    print("==== 有效源（按速度排序）====")
    for r in ok:
        print(f"{r['kbps']:>8} KB/s  ttfb={r.get('ttfb','?')}s  {r['name']},{r['url']}")

    out = path.rsplit(".", 1)[0] + "_valid.txt"
    with open(out, "w", encoding="utf-8") as f:
        for r in ok:
            f.write(f"{r['name']},{r['url']}\n")
    print(f"\n有效源已写入: {out}")


if __name__ == "__main__":
    main()
