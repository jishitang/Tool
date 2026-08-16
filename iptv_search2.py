# -*- coding: utf-8 -*-
"""
IPTV 资源搜索工具 v2 —— 多站搜索 + 自动过墙 + 去重写 test.txt
用法:
    python3 iptv_search.py <关键词> [--limit N] [--out test.txt]
    例: python3 iptv_search.py CCTV5
        python3 iptv_search.py 朱元璋 --limit 30

特性:
    - 多个站点依次搜索，命中内容跨站去重后写入 test.txt（重复条目只写一次）
    - 搜索页有 slowAES JS 反爬墙，脚本内置纯 Python AES 自动算 __test cookie 过墙
    - 结果 .txt 文件无墙，直接下载；自动识别 UTF-8 / GBK 编码
    - 零第三方依赖，裸 Python3 即可运行（含无 ssl 的精简环境）
"""
import urllib.request, urllib.parse, re, sys, time, argparse, os, subprocess, glob
from concurrent.futures import ThreadPoolExecutor

# 磁盘缓存：下载过的源文件存这里，下次同 url+大小直接读、不重下
CACHE_DIR = os.path.expanduser('~/tool/iptv_cache')
def _cache_key(url):
    return re.sub(r'[^\w.-]', '_', url)[:160]
def _cache_path(url, size):
    return os.path.join(CACHE_DIR, '%s__%s.cache' % (_cache_key(url), size))

# ======================= 纯 Python AES-128（slowAES 过墙用，零依赖）=======================
def _aes_tables():
    sbox = [0]*256
    p = q = 1
    while True:
        p = p ^ ((p << 1) & 0xff) ^ (0x1b if p & 0x80 else 0)
        q ^= (q << 1) & 0xff; q ^= (q << 2) & 0xff; q ^= (q << 4) & 0xff
        q ^= 0x09 if q & 0x80 else 0; q &= 0xff
        x = q ^ ((q << 1) | (q >> 7)) ^ ((q << 2) | (q >> 6)) ^ ((q << 3) | (q >> 5)) ^ ((q << 4) | (q >> 4))
        sbox[p] = (x ^ 0x63) & 0xff
        if p == 1:
            break
    sbox[0] = 0x63
    inv = [0]*256
    for i, v in enumerate(sbox):
        inv[v] = i
    return sbox, inv
_SBOX, _INV = _aes_tables()
_RCON = [0x01,0x02,0x04,0x08,0x10,0x20,0x40,0x80,0x1b,0x36]

def _mul(a, b):
    r = 0
    for _ in range(8):
        if b & 1: r ^= a
        hi = a & 0x80; a = (a << 1) & 0xff
        if hi: a ^= 0x1b
        b >>= 1
    return r

def _expand(key):
    w = [list(key[i*4:i*4+4]) for i in range(4)]
    for i in range(4, 44):
        t = list(w[i-1])
        if i % 4 == 0:
            t = t[1:] + t[:1]
            t = [_SBOX[x] for x in t]
            t[0] ^= _RCON[i//4 - 1]
        w.append([w[i-4][j] ^ t[j] for j in range(4)])
    return w

def _aes128_decrypt_block(ct, key):
    w = _expand(key)
    s = [[ct[r + 4*c] for c in range(4)] for r in range(4)]
    def add(rnd):
        for c in range(4):
            for r in range(4):
                s[r][c] ^= w[rnd*4 + c][r]
    add(10)
    for rnd in range(9, 0, -1):
        for r in range(1, 4):
            s[r] = s[r][-r:] + s[r][:-r]
        for r in range(4):
            for c in range(4):
                s[r][c] = _INV[s[r][c]]
        add(rnd)
        for c in range(4):
            a = [s[r][c] for r in range(4)]
            s[0][c] = _mul(a[0],14) ^ _mul(a[1],11) ^ _mul(a[2],13) ^ _mul(a[3],9)
            s[1][c] = _mul(a[0],9)  ^ _mul(a[1],14) ^ _mul(a[2],11) ^ _mul(a[3],13)
            s[2][c] = _mul(a[0],13) ^ _mul(a[1],9)  ^ _mul(a[2],14) ^ _mul(a[3],11)
            s[3][c] = _mul(a[0],11) ^ _mul(a[1],13) ^ _mul(a[2],9)  ^ _mul(a[3],14)
    for r in range(1, 4):
        s[r] = s[r][-r:] + s[r][:-r]
    for r in range(4):
        for c in range(4):
            s[r][c] = _INV[s[r][c]]
    add(0)
    return bytes(s[r][c] for c in range(4) for r in range(4))

def _slowaes_token(a_hex, b_hex, c_hex):
    """复刻 slowAES.decrypt(c, CBC, key=a, iv=b) 再 toHex。"""
    iv = bytes.fromhex(b_hex)
    dec = _aes128_decrypt_block(bytes.fromhex(c_hex), bytes.fromhex(a_hex))
    return bytes(dec[i] ^ iv[i] for i in range(16)).hex()

# ============================== 网络 / 解析 ==============================
SITES = [
    "http://ke.hongdus.com/_kk.php",
    "http://ke.html-5.me/_kk.php",
    "http://jk.html-5.me/_kk.php",
    "http://ge.html-5.me/_kk.php",
]
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
VERIFY = 'kong'                       # 各站固定验证码
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 禁用环境代理，直连

def _req(url, data=None, cookie=None, timeout=25):
    h = {'User-Agent': UA}
    if cookie:
        h['Cookie'] = cookie
    if data is not None:
        h['Content-Type'] = 'application/x-www-form-urlencoded'
    return _opener.open(urllib.request.Request(url, data=data, headers=h), timeout=timeout).read()

def _smart_decode(raw):
    """先按 UTF-8 严格解，失败再退 GBK；返回 (文本, 编码)。"""
    try:
        return raw.decode('utf-8'), 'utf-8'
    except UnicodeDecodeError:
        return raw.decode('gb18030', 'ignore'), 'gb18030'

def solve_wall(base):
    """取 slowAES 常量算 __test cookie；无墙返回 ''。"""
    html = _req(base).decode('latin-1')        # 常量是 ascii 十六进制，latin-1 不会出错
    m = re.findall(r'toNumbers\("([0-9a-f]+)"\)', html)
    if len(m) < 3:
        return ''
    return "__test=" + _slowaes_token(m[0], m[1], m[2])   # m = key, iv, ciphertext

def search(base, keyword, cookie):
    """POST 搜索，返回 ([(文件URL, 大小KB), ...], 总数, 页面编码)。过墙失败返回 (None, None, None)。"""
    body = urllib.parse.urlencode({
        'search_scope': 'content', 'sort': 'time',
        'keyword': keyword, 'verify_input': VERIFY,
    }).encode()
    raw = _req(base, body, cookie)
    if b'slowAES' in raw:
        return None, None, None
    html, enc = _smart_decode(raw)
    links = re.findall(r'href="([^"]+\.txt)"', html)
    # 每个 .txt 链接后面那个 <td>大小</td>（KB）；解析不到则留空
    size_map = dict(re.findall(r'href="([^"]+\.txt)"[^>]*>[^<]*</a>\s*</td>\s*<td>\s*([\d.]+)', html, re.S))
    items = [(urllib.parse.urljoin(base + '/', L), size_map.get(L, '')) for L in links]
    m = re.search(r'共\s*(\d+)\s*个文件', html)
    total = m.group(1) if m else str(len(items))   # 取不到计数时用链接数（0 结果就显示 0）
    return items, total, enc

def _quote(url, enc):
    p = urllib.parse.urlsplit(url)
    path = urllib.parse.quote(p.path, safe='/', encoding=enc, errors='ignore')
    return urllib.parse.urlunsplit((p.scheme, p.netloc, path, p.query, p.fragment))

def download_txt(url, enc, size='', use_cache=True, retries=1, deadline=40, maxbytes=50_000_000):
    # 返回 (文本或None, 来源: 'cache'/'net'/'fail')
    # 读到 EOF；用「时间上限 deadline + 字节上限 maxbytes」挡无限流/超慢源。
    # 磁盘缓存：同 url+size 命中直接读；只缓存完整下载(EOF)，截断的不缓存。
    cp = _cache_path(url, size) if (use_cache and size) else None
    if cp and os.path.exists(cp):
        try:
            return open(cp, encoding='utf-8', errors='ignore').read(), 'cache'
        except Exception:
            pass
    for i in range(retries + 1):
        try:
            r = _opener.open(urllib.request.Request(_quote(url, enc),
                             headers={'User-Agent': UA}), timeout=15)
            buf = bytearray(); t0 = time.time(); complete = False
            try:
                while len(buf) < maxbytes and (time.time() - t0) < deadline:
                    chunk = r.read(262144)
                    if not chunk:             # EOF：有限文件读完
                        complete = True
                        break
                    buf.extend(chunk)
            finally:
                r.close()
            text = _smart_decode(bytes(buf))[0]
            if cp and complete and text:       # 只缓存完整下载
                try:
                    os.makedirs(CACHE_DIR, exist_ok=True)
                    for old in glob.glob(os.path.join(CACHE_DIR, _cache_key(url) + '__*.cache')):
                        os.remove(old)         # 清同 url 的旧 size 版本
                    open(cp, 'w', encoding='utf-8').write(text)
                except Exception:
                    pass
            return text, 'net'
        except Exception:
            if i < retries:
                time.sleep(0.3)
            else:
                return None, 'fail'

# 剧集特征：第N集/期/话/回（含中文数字）、S01E02、EP01 —— 命中即视为剧集（默认丢弃）
EP_RE = re.compile(
    r'[第笫苐]\s*[0-9０-９零一二三四五六七八九十百千两]+\s*[集期话回]'   # 第/笫/苐 02集(认错别字+全角数字) / 第十八集 / 第3期
    r'|[Ss]\d{1,2}[Ee]\d{1,3}'                              # S01E02
    r'|(?<![A-Za-z])[Ee][Pp]\s*\.?\s*\d{1,4}'              # EP01 / ep.1
)

def _split_urls(raw):
    """把可能用 # 拼接的多个源拆成单个 url 列表（一行多源→多个）。"""
    urls = []
    for part in raw.split('#'):
        m = re.search(r'https?://\S+', part)
        if m:
            urls.append(m.group(0))
    return urls

def extract(content, keyword):
    """抽出含关键词的条目，返回 (名称, 网址) 列表。

    兼容三种来源：
      ① 名称,URL          单行          → 名称=URL 前面那段
      ② #EXTINF:...,名称 + 下一行 URL    → 名称=最后一个逗号后那段，URL=下一行
      ③ 名称 在一行、URL 在下一行         → 名称=本行，URL=下一行
    名称与网址分开返回，由调用方按"同址"去重、按"同名"加 -1/-2 标号。
    """
    # 归一化分隔符：字面转义 "\r\n"/"\r"/"\n" 和 真 CR/CRLF 全转成真换行。
    # 修畸形采集源（分隔符被写成文本"\r\n"或本就无换行）导致整文件挤成一行 → 关键词假命中 → 吐垃圾。
    content = (content.replace('\\r\\n', '\n').replace('\\r', '\n').replace('\\n', '\n')
                      .replace('\r\n', '\n').replace('\r', '\n'))
    lines = content.split('\n')
    n = len(lines)
    out = []
    for i, line in enumerate(lines):
        s = line.strip()
        if not s or keyword not in s:
            continue
        # JSON 配置大坨（TVBox 接口/直播配置）：不抽 url 测速（多为直播/网关，url 无意义），
        # 只截关键词所在 "name" 字段及前后各一项，留档供眼看大概是什么内容。url 留空→不进 test.txt/测速。
        if len(s) > 1000 and ('"name"' in s or s.lstrip()[:2] in ('[{', '{"')):
            names = re.findall(r'"name"\s*:\s*"([^"]*)"', s)
            hit = False
            for idx, nm in enumerate(names):
                if keyword in nm:
                    hit = True
                    out.append(('〔JSON〕' + ' ｜ '.join(names[max(0, idx - 1):idx + 2]), ''))
            if not hit:                       # 关键词不在 name 字段→给个字符窗口兜底
                for m in re.finditer(re.escape(keyword), s):
                    seg = s[max(0, m.start() - 80):m.end() + 80].replace(',', '，').replace('\n', ' ')
                    out.append(('〔JSON窗口〕' + seg, ''))
            continue
        # 畸形巨行（纯空格分隔的 blob，归一化后仍无换行）：正则逐条抽 名称,url，
        # 只留名字含关键词的，避免把整坨当一条 _split_urls 乱切吐垃圾。
        if len(s) > 1000 and s.count(',http') > 3:
            for nm, u in re.findall(r'([^\s,]+),\s*(https?://[^\s,]+)', s):
                if keyword in nm:
                    out.append((nm.strip(), u))
            continue
        if s.endswith('#genre#'):
            # 分组表头命中关键词 → 抽该组所有频道行(到下一个 #genre# 为止), 名称前缀组名便于辨认。
            # 这些多为"第N集"分集: 会进 test_raw 留档; test.txt 仍由阶段3「丢剧集」挡掉。
            gname = s.rsplit(',', 1)[0].strip()
            j = i + 1
            while j < n:
                t = lines[j].strip()
                if t.endswith('#genre#'):
                    break
                if not t:
                    j += 1; continue
                if t.startswith('#EXTINF'):
                    nm = t.rsplit(',', 1)[-1].strip() if ',' in t else t
                    for u in _split_urls(lines[j + 1].strip() if j + 1 < n else ''):
                        out.append((('%s %s' % (gname, nm)).strip(), u))
                    j += 2; continue
                if 'http' in t:
                    mm = re.search(r'https?://\S+', t)
                    nm = (t[:mm.start()] if mm else '').strip().rstrip(',').strip()
                    for u in _split_urls(t[mm.start():] if mm else ''):
                        out.append((('%s %s' % (gname, nm)).strip(), u))
                j += 1
            continue
        if s.startswith('#EXTINF'):
            name = s.rsplit(',', 1)[-1].strip() if ',' in s else s   # 逗号后面那部分=频道名
            urlline = lines[i + 1].strip() if i + 1 < n else ''
            for u in _split_urls(urlline):                            # 一行多源(#拼接)拆开
                out.append((name, u))
        elif 'http' in s:
            m = re.search(r'https?://\S+', s)
            name = (s[:m.start()] if m else s).strip().rstrip(',').strip()
            for u in _split_urls(s[m.start():] if m else ''):
                out.append((name, u))
        else:
            nxt = lines[i + 1].strip() if i + 1 < n else ''
            if nxt.startswith('http'):       # 仅当下一行是「裸地址」才配对；名称,url 行留给它自己那轮
                for u in _split_urls(nxt):
                    out.append((s, u))
    return out

def main():
    ap = argparse.ArgumentParser(description='多站 IPTV 资源搜索 + 去重写 test.txt')
    ap.add_argument('keyword', nargs='?')
    ap.add_argument('--limit', type=int, default=0, help='每站最多下载多少个文件(0=全部)')
    ap.add_argument('--out', default='test.txt', help='输出文件(默认 test.txt)')
    ap.add_argument('--no-check', action='store_true',
                    help='不测速，test.txt 保留原始结果（默认会测速、只保留能用的源覆盖到 --out）')
    ap.add_argument('--workers', type=int, default=16, help='测速并发数（默认16）')
    ap.add_argument('--keep-ep', action='store_true',
                    help='保留剧集（默认丢弃名字像剧集的：第N集/EP01/S01E01，只留轮播单地址源）')
    ap.add_argument('--deadline', type=int, default=40,
                    help='每个文件最多下载秒数（源文件大且服务器慢时调大，如 300；默认40）')
    ap.add_argument('--no-cache', action='store_true',
                    help=f'不用磁盘缓存（默认会把完整下载的文件缓存到 {CACHE_DIR}，下次同文件不重下）')
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    kw = args.keyword or input("请输入搜索关键词: ").strip()
    if not kw:
        print("关键词不能为空"); sys.exit(1)

    print(f"搜索: {kw}    站点 {len(SITES)} 个" + (f"，每站限 {args.limit} 个文件" if args.limit else ""))

    # 阶段 1：各站过墙 + 搜索，汇总全部文件链接（站内按大小去重，同大小=同文件不重下）
    tasks = []                       # [(link, enc), ...]
    for base in SITES:
        host = urllib.parse.urlsplit(base).netloc
        try:
            cookie = solve_wall(base)
            items, total, enc = search(base, kw, cookie)
        except Exception as e:
            print(f"  {host}: [失败] {e}")
            continue
        if items is None:
            print(f"  {host}: [过墙失败]")
            continue
        seen_sz, links = set(), []           # 站内同大小去重
        for L, sz in items:
            try:
                if sz and float(sz) > 3000:  # >3MB 的不是播放列表，下载前就跳过
                    continue
            except ValueError:
                pass
            if sz and sz in seen_sz:         # 大小已出现过 → 视为同文件，跳过
                continue
            seen_sz.add(sz)
            links.append((L, sz))            # 保留 size 供缓存命中判断
        if args.limit:
            links = links[:args.limit]
        print(f"  {host}: 命中 {total} 个，去同大小后取 {len(links)} 个")
        tasks.extend((L, enc, sz) for L, sz in links)

    # 阶段 2：全部文件一个大池并发下载（缓存命中的不重下；掩盖跨国高延迟）
    print(f"\n并发下载 {len(tasks)} 个文件 ...")
    with ThreadPoolExecutor(max_workers=32) as pool:
        dl = list(pool.map(lambda t: download_txt(t[0], t[1], size=t[2],
                                                  use_cache=not args.no_cache,
                                                  deadline=args.deadline), tasks))
    contents = [d[0] for d in dl]
    hits = sum(1 for d in dl if d[1] == 'cache')
    nets = sum(1 for d in dl if d[1] == 'net')
    ok = sum(1 for c in contents if c)
    print(f"  完成 {ok}/{len(tasks)}（缓存命中 {hits}，新下载 {nets}，失败 {len(tasks) - ok}）")

    # 阶段 3：抽取(每个文件符合关键字的条目，按文件分组) + 丢剧集 + 按网址去重 + 同名加 -N
    seen, name_count, results = set(), {}, []
    raw_sections = []                             # test_raw 按文件分组：每个文件一段
    total_match = dropped_ep = dropped_nourl = dropped_dup = 0
    for task, c in zip(tasks, contents):
        src = task[0]                             # 该内容来自哪个文件 url
        if c is None:
            raw_sections.append("===== %s （下载失败/无内容）=====" % src)
            continue
        fm = extract(c, kw)                        # 这个文件里符合关键字的条目
        total_match += len(fm)
        sec = ["===== %s （%d 条）=====" % (src, len(fm))]
        sec += ["%s,%s" % (n, u) for n, u in fm]
        raw_sections.append('\n'.join(sec))
        for name, url in fm:
            if not url.startswith('http'):       # 无地址（#genre# 头 / 残行）
                dropped_nourl += 1
                continue
            if not args.keep_ep and EP_RE.search(name):   # 默认丢弃剧集（只留轮播单地址源）
                dropped_ep += 1
                continue
            key = url.strip().rstrip('/')         # 归一化：去尾部 / 和空白，防"看着一样"的漏网
            if key in seen:                       # 同址去重（同名不同址=两个源，都留）
                dropped_dup += 1
                continue
            seen.add(key)
            name_count[name] = name_count.get(name, 0) + 1
            results.append(f"{name}-{name_count[name]},{url}")   # 同名加 -1/-2 区分

    # test_raw.txt = 各文件符合关键字的条目(去重/过滤前)，按文件分组留档供检查
    raw_path = args.out.rsplit('.', 1)[0] + '_raw.txt'
    with open(raw_path, 'w', encoding='utf-8') as f:
        f.write('\n\n'.join(raw_sections) + ('\n' if raw_sections else ''))
    print(f"  符合关键词 {total_match} 条(按文件分组) -> {raw_path}"
          f"（丢: 无地址{dropped_nourl} / 剧集{dropped_ep} / 重复{dropped_dup}）")

    with open(args.out, 'w', encoding='utf-8') as f:
        f.write('\n'.join(results) + ('\n' if results else ''))
    print(f"\n{'=' * 50}\n共 {len(results)} 条去重结果 -> {args.out}")

    # 默认搜完立即测速：调 iptv_check.py，用「能用的源」覆盖 --out（--no-check 可跳过）
    if not args.no_check and results:
        here = os.path.dirname(os.path.abspath(__file__))
        checker = os.path.join(here, 'iptv_check.py')
        if not os.path.exists(checker):
            print(f"⚠ 找不到 {checker}，跳过测速")
        else:
            print(f"\n>>> 开始测速（并发 {args.workers}）...")
            # 流式透传 iptv_check 进度，但吞掉它"有效源已写入 xxx_valid.txt"那句(会让人误以为多了个文件)
            proc = subprocess.Popen([sys.executable, checker, args.out, str(args.workers)],
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, bufsize=1)
            for line in proc.stdout:
                if '有效源已写入' in line:        # 吞掉这句，避免误以为多了个文件
                    continue
                print(line, end='', flush=True)
            proc.wait()
            valid = args.out.rsplit('.', 1)[0] + '_valid.txt'   # iptv_check 输出 <base>_valid.txt
            if os.path.exists(valid):
                os.replace(valid, args.out)                     # 有效源覆盖回 test.txt（test_valid.txt 即并入）
                try:
                    cnt = sum(1 for _ in open(args.out, encoding='utf-8'))
                except Exception:
                    cnt = '?'
                print(f"\n✅ {args.out} 已更新为可用源（{cnt} 条，按速度排序）")
            else:
                print("⚠ 未生成有效源文件，test.txt 仍为未测速的原始结果")

if __name__ == '__main__':
    main()
