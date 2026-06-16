#!/usr/bin/python
# -*- coding: utf-8 -*-
# 毒舌电影 (dushe05.com) CSP 爬虫
# - init: 解 cdndefend JS-PoW 验证拿 cookie, 抓搜索 token
# - 搜索: /search?k=词&t=token   详情: /detail/{id}.html   播放页: /play/{id}-{sid}-{eid}.html (内含 m3u8)
import re, json, base64, hashlib, requests
from urllib.parse import quote, unquote, urljoin
from base.spider import Spider

requests.packages.urllib3.disable_warnings(requests.packages.urllib3.exceptions.InsecureRequestWarning)

VER="v5"  # 改版标记: 每次改完 +1; 放在简介开头 [vN], 用来确认 App 加载了新文件
          # (不放标题, 因为标题带标记会破坏 TMDB 海报匹配)

class Spider(Spider):
    def getName(self): return "毒舌电影"
    def init(self, extend=""):
        self.host="https://www.dushe05.com"
        self.ua="Mozilla/5.0 (Linux; Android 12; Pixel) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36"
        self.token=""
        self.session=requests.Session()
        self.session.verify=False
        self.session.headers.update({"User-Agent":self.ua,"Referer":self.host+"/","Accept":"text/html,*/*","Accept-Language":"zh-CN,zh;q=0.9"})
        self._warm()
    def destroy(self):
        try: self.session.close()
        except Exception: return None

    # ---- cdndefend JS-PoW: SHA1(c+i) 字节 [n1]==0xb0 且 [n1+1]==0x0b ----
    def _solve(self,html):
        m=re.search(r"'([0-9A-Fa-f]{40})'",html)
        if not m: return None
        c=m.group(1); n1=int(c[0],16); i=0
        while i<5000000:
            d=hashlib.sha1((c+str(i)).encode()).digest()
            if d[n1]==0xb0 and d[n1+1]==0x0b:
                return "cdndefend_js_cookie="+c+str(i)
            i+=1
        return None
    def _warm(self):
        try:
            h=self._get("/")
            t=re.search(r"[?&](?:amp;)?t=([^\"'&<> ]+)",h)
            if t: self.token=unquote(t.group(1))
        except Exception: pass
    def _get(self,path,ref=""):
        url=self.host+path if path.startswith("/") else path
        try:
            r=self.session.get(url,headers={"Referer":ref or self.host+"/"},timeout=20)
            r.encoding="utf-8"
            txt=r.text
            if "verifying your browser" in txt[:400] or "cdndefend" in txt[:200]:
                ck=self._solve(txt)
                if ck:
                    k,v=ck.split("=",1)
                    self.session.cookies.set(k,v,domain="www.dushe05.com",path="/")
                    r=self.session.get(url,headers={"Referer":ref or self.host+"/"},timeout=20)
                    r.encoding="utf-8"; txt=r.text
            return txt
        except Exception:
            return ""

    def _pic(self,h):
        m=re.search(r'(https?://[^"\']+?\.(?:jpg|jpeg|png|webp))',h)
        return m.group(1) if m else ""
    def _cards(self,html):
        """从搜索/列表页提取 vod 卡片"""
        out=[]; seen=set()
        # 每个详情链接块: <a href="/detail/ID.html" ... title 或 内含文字>
        for m in re.finditer(r'href="/detail/(\d+)\.html"([^>]*)>(.*?)</a>',html,re.S):
            vid=m.group(1)
            if vid in seen: continue
            attr=m.group(2); inner=m.group(3)
            tm=re.search(r'title="([^"]+)"',attr) or re.search(r'alt="([^"]+)"',inner)
            name=tm.group(1).strip() if tm else re.sub(r'<[^>]+>',' ',inner)
            name=re.sub(r'\s+',' ',name).strip()
            if not name or len(name)>60: continue
            seen.add(vid)
            pic=""
            pm=re.search(r'(?:data-original|data-src|src)="(https?://[^"]+?\.(?:jpg|jpeg|png|webp))"',inner)
            if pm: pic=pm.group(1)
            rm=re.search(r'(?:class="[^"]*(?:note|remarks|score|msg)[^"]*"[^>]*>)([^<]{1,20})',inner)
            out.append({"vod_id":vid,"vod_name":name,"vod_pic":pic,"vod_remarks":(rm.group(1).strip() if rm else "")})
        return out

    def homeContent(self,filter):
        h=self._get("/")
        # 分类导航
        cls=[]; seen=set()
        for m in re.finditer(r'href="/(?:type|vodtype|list|show)/(\d+)[^"]*\.html"[^>]*>([^<]{1,8})</a>',h):
            cid,cname=m.group(1),m.group(2).strip()
            if cid in seen or not cname: continue
            seen.add(cid); cls.append({"type_id":cid,"type_name":cname})
        return {"class":cls[:12],"list":self._cards(h)[:40]}
    def homeVideoContent(self):
        return {"list":self._cards(self._get("/"))[:40]}
    def categoryContent(self,tid,pg,filter,extend):
        page=int(pg) if str(pg).isdigit() else 1
        for fmt in (f"/type/{tid}-{page}.html",f"/list/{tid}-{page}.html",f"/show/{tid}--------{page}---.html"):
            h=self._get(fmt)
            cards=self._cards(h)
            if cards:
                return {"list":cards,"page":page,"pagecount":page+1 if len(cards)>=20 else page,"limit":len(cards),"total":9999}
        return {"list":[],"page":page,"pagecount":1,"limit":0,"total":0}
    def searchContent(self,key,quick,pg="1"):
        if not self.token: self._warm()
        h=self._get(f"/search?k={quote(key)}&t={quote(self.token,safe='')}")
        return {"list":self._cards(h),"page":1,"pagecount":1,"limit":30,"total":0}

    def detailContent(self,ids):
        vid=ids[0]
        h=self._get(f"/detail/{vid}.html",self.host+"/")
        name=""
        for pat in (r'<h1[^>]*>\s*([^<]{1,40})', r'class="[^"]*(?:vod[-_ ]?name|video-title|detail[-_ ]?title)[^"]*"[^>]*>\s*([^<]{1,40})', r'<title>\s*([^<\-_]{1,40})'):
            mt=re.search(pat,h)
            if mt and mt.group(1).strip(): name=mt.group(1).strip(); break
        name=name or vid   # 标题保持干净, TMDB 才匹配得上
        pic=self._pic(h)
        # 年份(TMDB 匹配更准): detail-tags 里的 4 位年份, 或 /show/ 链接里的年份
        ym=re.search(r'detail-tags-item"[^>]*>\s*((?:19|20)\d{2})\s*<',h) or re.search(r'/show/[^"]*?-((?:19|20)\d{2})--',h) or re.search(r'>((?:19|20)\d{2})<',h)
        year=ym.group(1) if ym else ""
        # 剧情简介: detail-desc 块(内有 <p> 标签, 要剥), 退回 meta description
        dm=re.search(r'class="detail-desc"[^>]*>(.*?)</div>',h,re.S) or re.search(r'name="description"\s+content="([^"]+)"',h)
        desc=""
        if dm:
            desc=re.sub(r'<[^>]+>',' ',dm.group(1))
            desc=re.sub(r'\s+',' ',desc).strip()
        # 按 sid 分线路, 收集 (eid, 集名)
        routes={}
        for m in re.finditer(r'href="(/play/'+re.escape(vid)+r'-(\d+)-(\d+)\.html)"([^>]*)>(.*?)</a>',h,re.S):
            href,sid,eid,attr,inner=m.group(1),m.group(2),m.group(3),m.group(4),m.group(5)
            label=re.sub(r'<[^>]+>',' ',inner); label=re.sub(r'\s+',' ',label).strip()
            tm=re.search(r'title="([^"]+)"',attr)
            if tm and (not label or len(label)>12): label=tm.group(1).strip()
            routes.setdefault(sid,[])
            if any(e[0]==href for e in routes[sid]): continue
            # 统一成 01/02 纯数字(跟电影驿站一致, 选集网格才紧凑); 优先用集名里的数字, 没有就用序号
            mnum=re.search(r'(\d{1,4})',label or "")
            label="%02d"%(int(mnum.group(1)) if mnum else (len(routes[sid])+1))
            routes[sid].append((href,label))
        # 线路真实名(超清1/4K/FF线路/蓝光...)+ 副标签(高清/720P/秒播/香港加速...): 顺序与选集组一致
        labels=re.findall(r'class="source-item-label">\s*([^<]+?)\s*</span>',h)
        subs=re.findall(r'class="source-item-sublabel">\s*([^<]+?)\s*</span>',h)
        pf,pu=[],[]; used=set()
        for n,(sid,eps) in enumerate(routes.items()):
            nm=labels[n].strip() if n<len(labels) and labels[n].strip() else "线路%d"%(n+1)
            sub=subs[n].strip() if n<len(subs) and subs[n].strip() else ""
            if sub: nm="%s(%s)"%(nm,sub)   # 例: 蓝光(高清)、FF线路(播放快/高清)、WJ线路(720P)
            nm=re.sub(r'[\$#]',' ',nm).strip()
            base=nm; k=2
            while nm in used: nm=base+str(k); k+=1   # 防重名被 App 合并
            used.add(nm); pf.append(nm)
            pu.append("#".join(lab.replace("#","＃").replace("$","￥")+"$"+href for href,lab in eps))
        # 封面留空 -> App 才会去取 TMDB 剧照填卡片(带 logo 封面会压制 TMDB, 全是 logo)
        cont=("[%s] %s"%(VER,desc)).strip()  # 简介开头放版本标记 + 真实剧情
        return {"list":[{"vod_id":vid,"vod_name":name,"vod_pic":"","vod_year":year,
                         "vod_content":cont,
                         "vod_play_from":"$$$".join(pf) if pf else "毒舌",
                         "vod_play_url":"$$$".join(pu)}]}

    def playerContent(self,flag,id,vipFlags):
        # id 是 /play/...html 路径
        h=self._get(id,self.host+"/")
        url=""
        pm=re.search(r'player_aaaa\s*=\s*(\{.*?\})\s*</script>',h,re.S) or re.search(r'player_aaaa\s*=\s*(\{.*?\});',h,re.S)
        if pm:
            try: url=json.loads(pm.group(1)).get("url","")
            except Exception: url=""
        if not url:
            um=re.search(r'"url"\s*:\s*"([^"]+?\.(?:m3u8|mp4)[^"]*)"',h) or re.search(r'(https?:[^"\'\\]+?\.(?:m3u8|mp4)[^"\'\\]*)',h)
            if um: url=um.group(1)
        url=url.replace("\\/","/")
        if not url: return {"parse":1,"jx":0,"url":""}
        return {"parse":0,"jx":0,"url":url,"header":{"User-Agent":self.ua,"Referer":self.host+"/"}}
