"""
IPTV 资源搜索工具
用法: python iptv_search.py <关键词>
示例: python iptv_search.py 朱元璋
"""
import urllib.request, urllib.parse, re, sys, time

try:
    import ssl
    _ssl_ctx = ssl.create_default_context()
    _ssl_ctx.check_hostname = False
    _ssl_ctx.verify_mode = ssl.CERT_NONE
except ImportError:
    _ssl_ctx = None

def download(url, max_retries=3):
    """下载 URL 内容，失败自动重试"""
    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.Request(url)
            kwargs = {'timeout': 30}
            if _ssl_ctx and url.startswith('https'):
                kwargs['context'] = _ssl_ctx
            resp = urllib.request.urlopen(req, **kwargs)
            return resp.read().decode('utf-8', errors='ignore')
        except Exception as e:
            if attempt < max_retries:
                print(f"  [重试 {attempt}/{max_retries}] {e}")
                time.sleep(1)
            else:
                print(f"  [下载失败] {url} - {e} (已重试{max_retries}次)")
                return None

def search_files(keyword):
    """搜索并返回文件链接列表"""
    data = urllib.parse.urlencode({
        'search_scope': 'content',
        'sort': 'time',
        'keyword': keyword,
        'verify_input': '666'
    }).encode()
    req = urllib.request.Request('http://ox.my.to/_kk.php', data=data)
    resp = urllib.request.urlopen(req, timeout=30)
    html = resp.read().decode('utf-8')

    # 提取文件链接
    links = re.findall(r'href="(http://ox\.my\.to/i[^"]+\.txt)"', html)
    # 提取总数
    total = re.search(r'共 (\d+) 个文件', html)
    total = total.group(1) if total else '?'
    return links, total

def extract_matches(url, keyword):
    """下载文件并提取包含关键词的条目"""
    content = download(url)
    if content is None:
        return []

    lines = content.split('\n')
    results = []

    for i, line in enumerate(lines):
        line_s = line.strip()
        if not line_s or keyword not in line_s:
            continue

        if line_s.startswith('#EXTINF'):
            # M3U 双行格式: #EXTINF:... 名称 + 下一行 URL
            url_line = lines[i + 1].strip() if i + 1 < len(lines) else ''
            results.append(f"{line_s}\n{url_line}")
        elif 'http' in line_s:
            # 单行格式: 名称,URL 或 名称 URL
            results.append(line_s)
        else:
            # 关键词在非 URL 行，检查下一行是否是 URL
            next_line = lines[i + 1].strip() if i + 1 < len(lines) else ''
            if next_line.startswith('http'):
                results.append(f"{line_s}\n{next_line}")
            else:
                results.append(line_s)

    return results

def main():
    sys.stdout.reconfigure(encoding='utf-8')

    if len(sys.argv) >= 2:
        keyword = sys.argv[1]
    else:
        keyword = input("请输入搜索关键词: ").strip()
        if not keyword:
            print("关键词不能为空")
            sys.exit(1)
    print(f"搜索: {keyword}")
    print(f"{'=' * 60}")

    links, total = search_files(keyword)
    print(f"找到 {total} 个文件，开始提取匹配内容...\n")

    all_results = []
    for idx, link in enumerate(links, 1):
        fname = link.split('/')[-1]
        fname = urllib.parse.unquote(fname)
        matches = extract_matches(link, keyword)
        if matches:
            print(f"--- [{idx}] {fname} ({len(matches)} 条) ---")
            for m in matches:
                print(m)
            print()
            all_results.extend(matches)

    print(f"{'=' * 60}")
    print(f"共找到 {len(all_results)} 条匹配结果")

if __name__ == '__main__':
    main()
