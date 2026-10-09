#!/usr/bin/python
# -*- coding: utf-8 -*-
# 电影驿站 (dyyz.top / dyyz.one→top) CSP 爬虫 —— 标准 MacCMS, 全明文无加密
# 搜索 /vodsearch/词----------页---.html | 详情 /voddetail/{id}.html
# 选集/线路 /vodplay/{id}-{sid}-{nid}.html | 播放 player_aaaa.url = 直链 m3u8
import re, json, requests
from urllib.parse import quote, unquote
from base.spider import Spider

requests.packages.urllib3.disable_warnings(requests.packages.urllib3.exceptions.InsecureRequestWarning)

# 海报懒加载(2026-10-09): 站点自家图床 imgapiappdownload.dyyztv.top 已死(302 跳广告页), 这类卡片海报永远出不来。
# 办法: vod_pic 先给 App 一个本地代理地址 proxy://do=py&siteKey=..&type=poster&name=片名, 列表立刻显示;
# App 异步加载图片时回调 localProxy -> 豆瓣(带Referer回吐字节) -> TMDB(302跳图) -> 都没有则 404(App 画首字)。
# 搜索/列表不等海报, 海报各自慢慢出。TMDB 用与 ds.py 同一只读 token(api.tmdb.org/images.tmdb.org 国内可访问)。
VER = "v4"   # 改版标记: v1 原版 / v2 IP直连+诊断 / v3 海报懒加载(App 搜索页不认, 弃用) / v4 死图床→文字海报(剧名+年份·状态; 详情页 剧名+年份·主演) + 详情字段(主演/导演/年份/地区)。显示在详情简介开头。
DEAD_IMG_HOSTS = ("imgapiappdownload.dyyztv.top",)
TMDB_TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJhdWQiOiIzNjI4MmNhYzM1Nzg2Y2ZiZDhhODVkNjZlNGQ2NTk0NSIsIm5iZiI6MTc4MDc1MTc1NC44MTksInN1YiI6IjZhMjQxZDhhZDJjZWZmMmM0YjA5MDhmMiIsInNjb3BlcyI6WyJhcGlfcmVhZCJdLCJ2ZXJzaW9uIjoxfQ.29KtT3PolioR2YyuWK9mzOAqkGlVyN2p2UI52m3oYaU"
TMDB_API = "https://api.tmdb.org/3"
TMDB_IMG = "https://images.tmdb.org/t/p/w342"

class Spider(Spider):
    def getName(self): return "电影驿站"
    def init(self, extend=""):
        # 镜像站(任一活的就用; _pick_host() 自动探活并切到第一个能开的真站点)。
        # ★ 只放能开 /vodsearch 的【真站点】, 别放 dyyz.pw / dyyz.ws 发布页(那是查域名的, 不是站点)。
        # ★ 与 dyyz.js 的 DYYZ_HOSTS 保持一致(去掉发布页): 域名过期/新增时两边同步改。
        # 官方永久发布页(自己查最新可用域名): 主 dyyz.pw / 备 dyyz.ws。
        self.hosts = ["https://www.dyyz.top", "https://www.dyyz.cc", "https://www.dyyz.one", "https://dyyz2.app"]
        self.host = self.hosts[0]                     # 占位, _pick_host() 会改成真正探通的那个
        self.ua = "Mozilla/5.0 (Linux; Android 12; Pixel) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36"
        self.session = requests.Session()
        self.session.verify = False
        self.session.trust_env = False      # 不走 App/系统代理: 站点只接国内 IP, 经海外出口会被拒(2026-10-08 实测)
        self.session.headers.update({"User-Agent": self.ua, "Referer": self.host + "/", "Accept-Language": "zh-CN,zh;q=0.9"})
        self.ip_mode = ""                     # 非空=「IP直连+Host头(不带SNI)」模式, 域名全被运营商重置时自动启用
        self._pick_host()
    def destroy(self):
        try: self.session.close()
        except Exception: return None

    def _pick_host(self):
        # 镜像探活: 挨个试 self.hosts, 第一个能开且像真站点(MacCMS 有 /voddetail 或 /vodsearch 链接)的就定下来。
        # 都没真站点特征但有响应的, 退用第一个能通的; 全挂停在最后一个, 后续仍可重试。
        # self.diag 记录每个镜像的结果(状态/异常/耗时), 全挂时 homeContent 会把它显示成分类名, 在 App 里就能看到原因。
        import time as _t
        self.diag = []
        fallback = None
        for hh in self.hosts:
            self.host = hh
            t0 = _t.time()
            try: h = self._get("/", timeout=8)        # 探活用短超时: 4 个镜像最多 32s, 别让 App 一直转圈
            except Exception: h = ""
            self.diag.append("%s %s %.1fs" % (hh.replace("https://", ""), self.last_err or ("ok %d字" % len(h)), _t.time() - t0))
            if not h: continue                        # DNS失败/超时/域名过期空响应 -> 试下一个
            if fallback is None: fallback = hh
            if "/voddetail/" in h or "/vodsearch" in h: return
        if fallback: self.host = fallback; return
        # 四个域名全连不上(用户家宽+移动流量实测「连接被重置」= 运营商按域名/SNI 拦) →
        # 改走「IP 直连 + Host 头」: TLS 里不带域名, 站点服务端已验证只认 Host 头也照常出页(2026-10-09 实测)。
        import socket
        tried = set()
        for hh in self.hosts:
            hn = hh.split("://", 1)[1]
            try: ip = socket.gethostbyname(hn)
            except Exception: ip = ""
            for cand in (ip, "172.83.158.154"):       # 先用现解析的 IP, 再用已知 IP 兜底
                if not cand or cand in tried: continue
                tried.add(cand)
                self.host = hh; self.ip_mode = cand
                h = self._get("/", timeout=8)
                self.diag.append("IP直连 %s@%s %s" % (hn, cand, self.last_err or ("ok %d字" % len(h))))
                if h and ("/voddetail/" in h or "/vodsearch" in h): return
        self.ip_mode = ""; self.host = self.hosts[0]

    last_err = ""
    def _get(self, path, ref="", timeout=20):
        url = self.host + path if path.startswith("/") else path
        hdr = {"Referer": ref or self.host + "/"}
        if self.ip_mode and path.startswith("/"):   # IP 直连: 地址换成 IP, 域名放 Host 头, TLS 不带 SNI
            url = "https://" + self.ip_mode + path
            hdr["Host"] = self.host.split("://", 1)[1]
        self.last_err = ""
        try:
            r = self.session.get(url, headers=hdr, timeout=timeout)
            try: r.encoding = "utf-8"
            except Exception: pass
            if r.status_code != 200: self.last_err = "http %s" % r.status_code
            return r.text
        except Exception as e:
            # 把 requests 一长串异常压成「类型+真因」: DNS解析失败 / 连接超时 / 读超时 / 被拒绝 / SSL / 连接被重置
            s = str(e)
            cause = ""
            for k, v in (("NameResolution", "DNS解析失败"), ("Name or service not known", "DNS解析失败"), ("getaddrinfo", "DNS解析失败"),
                         ("ConnectTimeout", "连接超时"), ("Read timed out", "读超时"), ("timed out", "超时"),
                         ("Connection refused", "连接被拒绝"), ("ECONNREFUSED", "连接被拒绝"), ("SSL", "SSL握手失败"),
                         ("Connection reset", "连接被重置"), ("RemoteDisconnected", "对方断开"), ("Network is unreachable", "网络不可达")):
                if k in s: cause = v; break
            self.last_err = (type(e).__name__ + ":" + (cause or s[:60]))[:90]
            return ""

    def _pic(self, inner):
        m = re.search(r'(?:data-original|data-src|data-echo|src)=["\'](https?://[^"\']+?\.(?:jpg|jpeg|png|webp|gif))', inner, re.I)
        return m.group(1) if m else ""
    def _cards(self, html):
        # 先建 id->标题(title 属性, 兼容前后顺序)
        names = {}
        for m in re.finditer(r'href="/voddetail/(\d+)\.html"[^>]*?title="([^"]+)"', html):
            names.setdefault(m.group(1), m.group(2).strip())
        for m in re.finditer(r'title="([^"]+)"[^>]*?href="/voddetail/(\d+)\.html"', html):
            names.setdefault(m.group(2), m.group(1).strip())
        out, seen = [], set()
        for m in re.finditer(r'<a\s+[^>]*?href="/voddetail/(\d+)\.html"[^>]*>(.*?)</a>', html, re.S):
            vid, inner = m.group(1), m.group(2)
            if vid in seen: continue
            seen.add(vid)
            name = names.get(vid, "")
            if not name:
                am = re.search(r'alt="([^"]+)"', inner)
                name = (am.group(1) if am else re.sub(r'<[^>]+>', ' ', inner)).strip()
            name = re.sub(r'\s+', ' ', name).strip()
            name = re.sub(r'(封面图|封面|海报|图片|剧照)$', '', name).strip()
            if not name or len(name) > 60: continue
            rm = re.search(r'class="[^"]*(?:note|remarks|continu|msg|pic-text|score|jidi|public-list-prb)[^"]*"[^>]*>\s*([^<]{1,20})', inner, re.I)
            remarks = rm.group(1).strip() if rm else ""
            # 卡片右侧文字块: thumb-else(年份/地区/类型) + thumb-director(导演) + 主演: 取到下一张卡为止, 别串卡
            tail = html[m.end():m.end() + 3000]
            nxt = tail.find('/voddetail/')
            if nxt > 0: tail = tail[:nxt]
            year, actor = "", ""
            te = re.search(r'thumb-else[^>]*>(.*?)</div>', tail, re.S)
            if te:
                ym = re.search(r'((?:19|20)\d{2})', re.sub(r'<[^>]+>', ' ', te.group(1)))
                if ym: year = ym.group(1)
            ta = re.search(r'主演[:：]\s*</a>(.*?)</div>', tail, re.S)
            if ta:
                actors = [a.strip() for a in re.findall(r'>\s*([^<>]{1,12}?)\s*</a>', ta.group(1)) if a.strip()]
                actor = " ".join(actors[:3])
            pic = self._pic(inner)
            if not pic or any(h in pic for h in DEAD_IMG_HOSTS):     # 死图床/无图 -> 文字海报: 剧名 / 年份·主演(没主演用状态), 参照 DS
                sub = " · ".join(x for x in (year, actor or remarks) if x)
                pic = self._titimg(name, sub)
            out.append({"vod_id": vid, "vod_name": name, "vod_pic": pic, "vod_year": year, "vod_actor": actor,
                        "vod_remarks": remarks})
        return out
    def _titimg(self, name, sub=""):
        """placehold.jp 把文字渲染成海报(中文OK, 支持 %0A 换行): 第1行剧名, 第2行副信息(年份·状态 / 年份·主演)。按名字哈希取深色底+白字。"""
        import hashlib
        t = (name or "无名").strip(); disp = t[:12]
        b = hashlib.md5(t.encode("utf-8")).digest()
        bg = "%02x%02x%02x" % (b[0] % 110, b[1] % 110, b[2] % 110)
        txt = quote(disp, safe="") + ("%0A" + quote((sub or "")[:16], safe="") if sub else "")
        return "https://placehold.jp/22/%s/ffffff/300x420.png?text=%s" % (bg, txt)

    def homeContent(self, filter):
        h = self._get("/")
        cls, seen = [], set()
        for m in re.finditer(r'href="/vodtype/(\d+)\.html"[^>]*>(?:<[^>]+>)*\s*([^<]{1,8})', h):
            cid, cname = m.group(1), m.group(2).strip()
            if cid in seen or not cname or cname in ("更多",): continue
            seen.add(cid); cls.append({"type_id": cid, "type_name": cname})
        if not cls:
            # 站点没拉到/页面不对: 把原因直接显示成分类, 点进去看每个镜像的探活结果(不用看日志)
            why = self.last_err or ("页面%d字无分类" % len(h))
            return {"class": [{"type_id": "__diag", "type_name": "⚠连不上站点:" + why[:30]}], "list": []}
        return {"class": cls[:15], "list": self._cards(h)[:60]}
    def _diag_list(self):
        rows = [{"vod_id": "__d%d" % i, "vod_name": d, "vod_pic": "", "vod_remarks": "探活"} for i, d in enumerate(getattr(self, "diag", []) or [])]
        rows.append({"vod_id": "__host", "vod_name": "当前host=" + self.host, "vod_pic": "", "vod_remarks": ""})
        rows.append({"vod_id": "__err", "vod_name": "最后错误=" + (self.last_err or "无"), "vod_pic": "", "vod_remarks": ""})
        return {"list": rows, "page": 1, "pagecount": 1, "limit": len(rows), "total": len(rows)}
    # ---------- 海报懒加载: 列表先出, 图片由 App 异步回调 localProxy 时再查 ----------
    def _lazy_pic(self, name):
        key = getattr(self, "siteKey", "") or "dyyz"
        # 优先用 App 给的绝对地址 http://127.0.0.1:端口/proxy?do=py (任何图片加载路径都认 http), 没有(本机测试)才用 proxy:// 简写
        base = ""
        try:
            if hasattr(self, "getProxyUrl"): base = self.getProxyUrl(True) or ""
        except Exception:
            base = ""
        if not base: base = "proxy://do=py"
        return "%s&siteKey=%s&type=poster&name=%s" % (base, key, quote(name, safe=""))
    WARM_POSTERS = False   # v4 起列表不再走懒加载(App 搜索页不认本地代理图), 预热关闭; localProxy 代码保留备用
    def _warm_posters(self, cards):
        if not self.WARM_POSTERS: return
        names = []
        cache = self.__dict__.setdefault("pcache", {})
        for c in cards:
            p = c.get("vod_pic", "")
            if ("type=poster" in p) and c.get("vod_name") and c["vod_name"] not in cache and c["vod_name"] not in names: names.append(c["vod_name"])
        if not names: return
        names = names[:30]
        import threading
        def run():
            try:
                from concurrent.futures import ThreadPoolExecutor
                with ThreadPoolExecutor(max_workers=3) as ex: list(ex.map(self._poster_lookup, names))
            except Exception:
                for n in names:
                    try: self._poster_lookup(n)
                    except Exception: pass
        threading.Thread(target=run, daemon=True).start()
    def _clean_name(self, name):
        # 去掉 "《》"、年份/版本后缀, 提高豆瓣/TMDB 命中
        n = name or ""
        m = re.search(r'《([^》]+)》', n)            # 有书名号就只取书名号里的
        if m: n = m.group(1)
        n = re.sub(r'\s*[\(（]\s*(?:19|20)\d{2}\s*[\)）]\s*$', '', n)
        n = re.sub(r'(?:电视剧|电影|动漫|综艺)?(?:全集|完整版|未删减版?)$', '', n)
        n = re.sub(r'\s*(?:第[一二三四五六七八九十\d]+[季部]|国语版?|粤语版?|中字|高清|HD|BD|4K)$', '', n)
        return n.strip(" -_·|:：") or name
    def _poster_lookup(self, name):
        """返回 ('bytes', 图片字节, mime) / ('url', 图片地址) / None。豆瓣优先(国内快, 图要 Referer 故回吐字节), 没中查 TMDB(302 跳图)。"""
        cache = self.__dict__.setdefault("pcache", {})
        if name in cache: return cache[name]
        res = None
        q = self._clean_name(name)
        try:
            r = requests.get("https://movie.douban.com/j/subject_suggest", params={"q": q},
                             headers={"User-Agent": self.ua, "Referer": "https://movie.douban.com/"}, timeout=6, verify=False)
            for it in (r.json() or []):
                img = it.get("img", "")
                if img:
                    img = img.replace("s_ratio_poster", "m_ratio_poster")
                    ri = requests.get(img, headers={"User-Agent": self.ua, "Referer": "https://movie.douban.com/"}, timeout=8, verify=False)
                    if ri.status_code == 200 and len(ri.content) > 2000:
                        res = ("bytes", ri.content, ri.headers.get("Content-Type", "image/jpeg").split(";")[0])
                    break
        except Exception:
            pass
        if res is None:
            try:
                r = requests.get(TMDB_API + "/search/multi", params={"query": q, "language": "zh-CN", "include_adult": "false"},
                                 headers={"accept": "application/json", "Authorization": "Bearer " + TMDB_TOKEN}, timeout=6, verify=False)
                for it in (r.json().get("results") or []):
                    if it.get("poster_path"):
                        res = ("url", TMDB_IMG + it["poster_path"]); break
            except Exception:
                pass
        if res is not None and len(cache) < 400: cache[name] = res   # 只缓存确定结果(含豆瓣字节, 几十KB/张, 上限400)
        return res
    def localProxy(self, param):
        """App 本地代理回调(vod_pic=proxy://...): 返回 [状态码, mime, 字节, 头]。"""
        try:
            p = param if isinstance(param, dict) else json.loads(param or "{}")
            if p.get("type") == "poster":
                name = unquote(p.get("name", ""))
                res = self._poster_lookup(name) if name else None
                if res and res[0] == "bytes":
                    return [200, res[2], res[1], {"Cache-Control": "max-age=86400"}]
                if res and res[0] == "url":
                    return [302, "text/plain", b"", {"Location": res[1], "Cache-Control": "max-age=86400"}]
                return [404, "text/plain", b"no poster", {}]
        except Exception as e:
            return [500, "text/plain", str(e).encode("utf-8"), {}]
        return [404, "text/plain", b"", {}]

    def homeVideoContent(self):
        return {"list": self._cards(self._get("/"))[:60]}
    def categoryContent(self, tid, pg, filter, extend):
        if tid == "__diag": return self._diag_list()
        page = int(pg) if str(pg).isdigit() else 1
        path = f"/vodtype/{tid}.html" if page == 1 else f"/vodtype/{tid}-{page}.html"
        cards = self._cards(self._get(path))
        return {"list": cards, "page": page, "pagecount": page + 1 if len(cards) >= 20 else page,
                "limit": len(cards), "total": 999999}
    def searchContent(self, key, quick, pg="1"):
        page = int(pg) if str(pg).isdigit() else 1
        h = self._get(f"/vodsearch/{quote(key)}----------{page}---.html")
        cards = self._cards(h)
        return {"list": cards, "page": page, "pagecount": page + 1 if len(cards) >= 18 else page,
                "limit": len(cards), "total": 999999}

    def _line_rank(self, nm):
        """静态线路排序分(越小越前)。116服务器实测6部剧(大秦×3+王保长×3)定的: 名字不能信(蓝光线路1看着像主线路其实最慢)。
        快: 线路6(318)/备用2(268)/超快1(244); 慢: 线路1(1)/线路4(2); 其余未测准给中档。想调改这里数字。"""
        if '线路6' in nm: return 5
        if '备用2' in nm: return 10
        if '超快1' in nm: return 15
        if re.search(r'线路4', nm): return 75
        if re.search(r'线路1(?!\d)', nm): return 80
        return 40

    def detailContent(self, ids):
        vid = ids[0]
        h = self._get(f"/voddetail/{vid}.html")
        title = ""
        for pat in [r'<h1[^>]*>\s*([^<]{1,40})', r'<h2[^>]*class="[^"]*(?:title|name)[^"]*"[^>]*>\s*([^<]{1,40})',
                    r'class="[^"]*(?:vod[-_ ]?name|detail[-_ ]?title|video-title)[^"]*"[^>]*>\s*([^<]{1,40})',
                    r'<title>\s*([^<\-_]{1,40})']:
            mt = re.search(pat, h)
            if mt and mt.group(1).strip():
                title = mt.group(1).strip(); break
        title = re.sub(r'(封面图|封面|海报|在线观看|免费观看).*$', '', title).strip()
        pic = self._pic(h)
        desc = re.search(r'(?:vod_content|class="[^"]*(?:content|jianjie|desc|blurb)[^"]*")[^>]*>\s*([^<]{6,})', h, re.I)
        # 详情字段(MacCMS: <li><em>主演：</em>...</li>): 主演/导演/年份/地区/类型/状态
        def _field(label):
            fm = re.search(r'<em[^>]*>\s*' + label + r'\s*[:：]\s*</em>(.*?)</li>', h, re.S)
            if not fm: return ""
            v = re.sub(r'<[^>]+>', ' ', fm.group(1)); v = re.sub(r'(?:&nbsp;|\s)+', ' ', v).strip(" ,，/")
            return v
        actor = _field("主演"); director = _field("导演"); year = _field("年份"); area = _field("地区"); typ = _field("类型"); status = _field("状态")
        if not year:
            ym = re.search(r'((?:19|20)\d{2})-\d{2}-\d{2}上映', h) or re.search(r'>\s*((?:19|20)\d{2})\s*<', h)
            if ym: year = ym.group(1)
        if not pic or any(x in pic for x in DEAD_IMG_HOSTS):       # 死图床 -> 文字海报: 剧名 / 年份·主演前3
            top3 = " ".join([a for a in actor.split(" ") if a][:3])
            pic = self._titimg(title or vid, " · ".join(x for x in (year, top3) if x))
        # 收集 (sid, nid, 集名)
        routes = {}
        for m in re.finditer(r'href="/vodplay/' + re.escape(vid) + r'-(\d+)-(\d+)\.html"[^>]*>\s*(?:<[^>]+>)*\s*([^<]{1,20})', h):
            sid, nid, label = m.group(1), m.group(2), re.sub(r'\s+', ' ', m.group(3)).strip()
            routes.setdefault(sid, [])
            href = f"/vodplay/{vid}-{sid}-{nid}.html"
            if not any(e[0] == href for e in routes[sid]):
                routes[sid].append((href, label or nid))
        # 线路真实名(swiper-slide: 蓝光线路6/蓝光备用2/蓝光超快1...) + 静态速度排序(116服务器实测6部剧)
        names = re.findall(r'swiper-slide[^>]*>(?:<i[^>]*></i>)?\s*(?:&nbsp;)?\s*([^<]+?)\s*<span class="badge"', h)
        lines = []; used = set()
        for n, (sid, eps) in enumerate(routes.items()):
            nm = names[n].strip() if n < len(names) and names[n].strip() else "线路%d" % (n + 1)
            nm = re.sub(r'[\$#]', ' ', nm).strip()
            b = nm; k = 2
            while nm in used: nm = b + str(k); k += 1   # 防重名被 App 合并
            used.add(nm)
            epstr = "#".join(lab.replace("#", "＃").replace("$", "￥") + "$" + href for href, lab in eps)
            lines.append((self._line_rank(nm), nm, epstr))
        lines.sort(key=lambda l: l[0])   # 静态速度排序: 快的在前, 慢的在后
        pf = [l[1] for l in lines]; pu = [l[2] for l in lines]
        return {"list": [{"vod_id": vid, "vod_name": title or vid, "vod_pic": pic,
                          "vod_actor": actor, "vod_director": director, "vod_year": year, "vod_area": area, "type_name": typ, "vod_remarks": status,
                          "vod_content": ("[" + VER + "] " + (desc.group(1).strip() if desc else "")).strip(),   # 简介开头带版本标记, 确认 App 加载的是新文件
                          "vod_play_from": "$$$".join(pf) if pf else "电影驿站",
                          "vod_play_url": "$$$".join(pu)}]}

    def playerContent(self, flag, id, vipFlags):
        # id 是 /vodplay/{id}-{sid}-{nid}.html
        h = self._get(id, self.host + "/")
        m = re.search(r'var\s+player_aaaa\s*=\s*(\{.*?\})\s*</script>', h, re.S) or re.search(r'player_aaaa\s*=\s*(\{.*?\});', h, re.S)
        url = ""
        if m:
            try: url = json.loads(m.group(1)).get("url", "")
            except Exception:
                um = re.search(r'"url"\s*:\s*"([^"]+)"', m.group(1))
                if um: url = um.group(1)
        url = url.replace("\\/", "/")
        if not url:
            um = re.search(r'(https?://[^"\'\\]+?\.(?:m3u8|mp4)[^"\'\\]*)', h)
            url = um.group(1).replace("\\/", "/") if um else ""
        if not url: return {"parse": 1, "jx": 0, "url": ""}
        return {"parse": 0, "jx": 0, "url": url, "header": {"User-Agent": self.ua, "Referer": self.host + "/"}}
