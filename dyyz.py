#!/usr/bin/python
# -*- coding: utf-8 -*-
# 电影驿站 (dyyz.top / dyyz.one→top) CSP 爬虫 —— 标准 MacCMS, 全明文无加密
# 搜索 /vodsearch/词----------页---.html | 详情 /voddetail/{id}.html
# 选集/线路 /vodplay/{id}-{sid}-{nid}.html | 播放 player_aaaa.url = 直链 m3u8
import re, json, requests
from urllib.parse import quote
from base.spider import Spider

requests.packages.urllib3.disable_warnings(requests.packages.urllib3.exceptions.InsecureRequestWarning)

class Spider(Spider):
    def getName(self): return "电影驿站"
    def init(self, extend=""):
        # 域名失效时改这里(镜像: dyyz.top / dyyz.cc / dyyz2.app)
        self.host = "https://www.dyyz.top"
        self.ua = "Mozilla/5.0 (Linux; Android 12; Pixel) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36"
        self.session = requests.Session()
        self.session.verify = False
        self.session.headers.update({"User-Agent": self.ua, "Referer": self.host + "/", "Accept-Language": "zh-CN,zh;q=0.9"})
    def destroy(self):
        try: self.session.close()
        except Exception: return None

    def _get(self, path, ref=""):
        url = self.host + path if path.startswith("/") else path
        try:
            r = self.session.get(url, headers={"Referer": ref or self.host + "/"}, timeout=20)
            try: r.encoding = "utf-8"
            except Exception: pass
            return r.text
        except Exception:
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
            rm = re.search(r'class="[^"]*(?:note|remarks|continu|msg|pic-text|score|jidi)[^"]*"[^>]*>\s*([^<]{1,20})', inner, re.I)
            out.append({"vod_id": vid, "vod_name": name, "vod_pic": self._pic(inner),
                        "vod_remarks": (rm.group(1).strip() if rm else "")})
        return out

    def homeContent(self, filter):
        h = self._get("/")
        cls, seen = [], set()
        for m in re.finditer(r'href="/vodtype/(\d+)\.html"[^>]*>(?:<[^>]+>)*\s*([^<]{1,8})', h):
            cid, cname = m.group(1), m.group(2).strip()
            if cid in seen or not cname or cname in ("更多",): continue
            seen.add(cid); cls.append({"type_id": cid, "type_name": cname})
        return {"class": cls[:15], "list": self._cards(h)[:60]}
    def homeVideoContent(self):
        return {"list": self._cards(self._get("/"))[:60]}
    def categoryContent(self, tid, pg, filter, extend):
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
                          "vod_content": (desc.group(1).strip() if desc else ""),
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
