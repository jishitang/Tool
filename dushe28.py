#!/usr/bin/python
# -*- coding: utf-8 -*-
# 毒舌电影 (dushe05.com) CSP 爬虫
# - init: 解 cdndefend JS-PoW 验证拿 cookie, 抓搜索 token
# - 搜索: /search?k=词&t=token   详情: /detail/{id}.html   播放页: /play/{id}-{sid}-{eid}.html (内含 m3u8)
import re, json, base64, hashlib, requests
from urllib.parse import quote, unquote, urljoin
from base.spider import Spider

requests.packages.urllib3.disable_warnings(requests.packages.urllib3.exceptions.InsecureRequestWarning)

VER="v28"  # 改版标记: 每次改完 +1; 放在简介开头 [vN], 用来确认 App 加载了新文件
# 内置 TMDB v4 read token(扩展参数留空时用它 -> 重导丢了扩展参数也有海报)。
# 只读, 风险低; 想换/作废到 themoviedb.org 后台重新生成即可。填了扩展参数则以扩展参数为准。
DEFAULT_TMDB="eyJhbGciOiJIUzI1NiJ9.eyJhdWQiOiIzNjI4MmNhYzM1Nzg2Y2ZiZDhhODVkNjZlNGQ2NTk0NSIsIm5iZiI6MTc4MDc1MTc1NC44MTksInN1YiI6IjZhMjQxZDhhZDJjZWZmMmM0YjA5MDhmMiIsInNjb3BlcyI6WyJhcGlfcmVhZCJdLCJ2ZXJzaW9uIjoxfQ.29KtT3PolioR2YyuWK9mzOAqkGlVyN2p2UI52m3oYaU"
          # (不放标题, 因为标题带标记会破坏 TMDB 海报匹配)

class Spider(Spider):
    def getName(self): return "毒舌电影"
    def init(self, extend=""):
        self.host="https://www.dushe05.com"
        self.ua="Mozilla/5.0 (Linux; Android 12; Pixel) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36"
        self.token=""
        self.cdcookie=""                                # cdndefend cookie 解出后存这(备用)
        # TMDB 真海报: 「扩展参数」填 v4 Token/v3 Key 则优先用它; 留空则用内置 DEFAULT_TMDB
        self.tmdb=(extend or "").strip() or DEFAULT_TMDB
        # nostr 实测: api.tmdb.org / images.tmdb.org 国内不用代理可访问(官方 api.themoviedb.org / image.tmdb.org 被墙)
        self.tmdb_api="https://api.tmdb.org/3"          # 想换镜像在扩展参数: token|api域名|图片域名
        self.tmdb_img="https://images.tmdb.org/t/p/w342"
        if "|" in self.tmdb:                            # 可选: token|apiBase|imgBase
            ps=self.tmdb.split("|"); self.tmdb=ps[0].strip()
            if len(ps)>1 and ps[1].strip(): self.tmdb_api=ps[1].strip().rstrip("/")
            if len(ps)>2 and ps[2].strip(): self.tmdb_img=ps[2].strip().rstrip("/")
        self.tmdb_dead=False                            # 查不通(被墙)就置真, 本会话不再查
        self.douban_dead=False                          # 豆瓣访问不了/被限流就置真, 本会话走TMDB
        self.db_miss=0                                  # 豆瓣连续空结果计数(多半被限流), 超阈值就放弃豆瓣
        self.dbcache={}                                 # 片名->豆瓣海报 缓存
        self.picache={}                                 # 片名->TMDB海报 缓存
        self.linecache={}                               # vodId->线路测速分 缓存(同剧再开不重测)
        self._catcache={}                               # (频道,站点页)->卡片 缓存(分批出海报不重复抓)
        self.session=requests.Session()
        self.session.verify=False
        self.session.headers.update({"User-Agent":self.ua,"Referer":self.host+"/","Accept":"text/html,*/*","Accept-Language":"zh-CN,zh;q=0.9"})
        self._warm()
        if not self.cdcookie:                           # 兜底: 从 session 里读已有的 cookie
            cv=self.session.cookies.get("cdndefend_js_cookie")
            if cv: self.cdcookie="cdndefend_js_cookie="+cv
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
                    self.cdcookie=ck                    # 存完整 cookie 串, 给封面代理用
                    r=self.session.get(url,headers={"Referer":ref or self.host+"/"},timeout=20)
                    r.encoding="utf-8"; txt=r.text
            return txt
        except Exception:
            return ""

    def _pic(self,h):
        m=re.search(r'(https?://[^"\']+?\.(?:jpg|jpeg|png|webp))',h)
        return m.group(1) if m else ""
    def _titimg(self,name):
        """dushe 真封面被 cdndefend 锁、加载不到 -> 用占位图服务把片名渲染成图(中文OK),
        每片按名字哈希取不同深色底+白字, 比 App 的大首字占位美观。"""
        t=(name or "无名").strip(); disp=t[:13]
        b=hashlib.md5(t.encode("utf-8")).digest()
        bg="%02x%02x%02x"%(b[0]%110,b[1]%110,b[2]%110)  # 深色, 白字才清楚
        return "https://placehold.jp/24/%s/ffffff/300x420.png?text=%s"%(bg,quote(disp,safe=''))
    def _tmdb_poster(self,name):
        """查 TMDB 海报 -> image.tmdb.org 地址。命中=url, 确定无匹配='', 网络抖动=None(不缓存/不全局禁用), 401=永久停。"""
        if not self.tmdb or self.tmdb_dead or not name: return ""
        if name in self.picache: return self.picache[name]   # 只缓存确定结果(url 或 ''), 不缓存网络失败
        try:
            params={"query":name,"language":"zh-CN","include_adult":"false"}
            hdr={"accept":"application/json"}
            if self.tmdb.startswith("eyJ") or len(self.tmdb)>50:   # v4 Token -> Bearer
                hdr["Authorization"]="Bearer "+self.tmdb
            else:                                                  # v3 Key -> query
                params["api_key"]=self.tmdb
            r=requests.get(self.tmdb_api+"/search/multi",params=params,headers=hdr,timeout=5,verify=False)
            if r.status_code in (401,403):          # token 无效 -> 唯一永久停的情况
                self.tmdb_dead=True; return ""
            pic=""
            for it in (r.json().get("results") or []):
                pp=it.get("poster_path")
                if pp: pic=self.tmdb_img+pp; break
            self.picache[name]=pic                  # 成功(命中或确定无匹配)才缓存
            return pic
        except Exception:
            return None                              # 网络抖动: 不缓存、不置 tmdb_dead -> 下次/别栏可重试
    def _fill_tmdb(self,cards):
        """给一批卡片并发填 TMDB 海报(查到的替换文字占位图)。网络抖动只跳过本批, 不影响别栏/已缓存。"""
        if not self.tmdb or self.tmdb_dead or not cards: return
        probe=self._tmdb_poster(cards[0]["vod_name"])
        if probe is None: probe=self._tmdb_poster(cards[0]["vod_name"])  # 抖动重试一次
        if probe is None or self.tmdb_dead: return   # 网络不通(只跳过本批) 或 token无效(永久停)
        def work(c):
            p=self._tmdb_poster(c["vod_name"])
            if p: c["vod_pic"]=p                      # None(抖动)/''(无匹配) -> 保留文字图
        try:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=16) as ex: list(ex.map(work,cards))
        except Exception:
            for c in cards: work(c)   # 并发不可用就顺序查
    def _douban_poster(self,name):
        """豆瓣海报 -> doubanio地址 + @Referer(绕防盗链, App的ImgUtil会带上referer)。国内快/国产剧准/不用代理。
        命中=url, 无匹配='', 网络抖动=None(置douban_dead, 本会话退TMDB/文字)。"""
        if self.douban_dead or not name: return ""
        if name in self.dbcache: return self.dbcache[name]
        try:
            r=requests.get("https://movie.douban.com/j/subject_suggest",params={"q":name},
                headers={"User-Agent":self.ua,"Referer":"https://movie.douban.com/"},timeout=6,verify=False)
            pic=""
            for it in (r.json() or []):
                img=it.get("img","")
                if img:
                    pic=img.replace("s_ratio_poster","m_ratio_poster")+"@Referer=https://movie.douban.com/"
                    break
            self.dbcache[name]=pic
            if pic: self.db_miss=0                      # 命中 -> 重置
            else:                                       # 空结果(多半被限流) -> 连续6次就放弃豆瓣, 余下走TMDB
                self.db_miss+=1
                if self.db_miss>=6: self.douban_dead=True
            return pic
        except Exception:
            self.douban_dead=True; return None
    def _fill_poster(self,cards):
        """填海报: 豆瓣优先(国内快/国产准) -> 没中再TMDB -> 都没保留文字图。并发别太高(豆瓣防爬)。"""
        if not cards: return
        if not self.douban_dead:                         # 豆瓣先探一张(抖动重试一次)
            probe=self._douban_poster(cards[0]["vod_name"])
            if probe is None:
                self.douban_dead=False; self.dbcache.pop(cards[0]["vod_name"],None)
                probe=self._douban_poster(cards[0]["vod_name"])
            if probe is None: self.douban_dead=True
        def work(c):
            p=("" if self.douban_dead else self._douban_poster(c["vod_name"]))
            if not p and not self.tmdb_dead: p=self._tmdb_poster(c["vod_name"])   # 豆瓣没中 -> TMDB兜底
            if p: c["vod_pic"]=p
        try:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=6) as ex: list(ex.map(work,cards))
        except Exception:
            for c in cards: work(c)
    def _cards(self,html):
        """从搜索/首页/频道页提取 vod 卡片。标题用障眼法(v-item-title: 水印 strong 隐藏, 真名居中), 过滤水印。"""
        out=[]; seen=set()
        def clean(t):
            t=re.sub(r'<[^>]+>',' ',t or ""); t=re.sub(r'\s+',' ',t).strip()
            t=t.replace("可可影视","").replace("kekys.com","").replace("kekys","")
            return t.strip(" -_.·|")
        for m in re.finditer(r'href="/detail/(\d+)\.html"([^>]*)>(.*?)</a>',html,re.S):
            vid=m.group(1)
            if vid in seen: continue
            attr=m.group(2); inner=m.group(3)
            name=""
            # 1) v-item-title 真名: 挑不含水印的那条(隐藏的水印条含"可可影视/kekys")
            for t in re.findall(r'class="[^"]*v-item-title[^"]*"[^>]*>\s*([^<]+?)\s*</div>',inner):
                if "可可影视" not in t and "kekys" not in t and t.strip(): name=t.strip(); break
            # 2) 退回 title/alt 属性
            if not name:
                tm=re.search(r'title="([^"]+)"',attr) or re.search(r'alt="([^"]+)"',inner)
                if tm and "可可影视" not in tm.group(1) and "kekys" not in tm.group(1): name=tm.group(1).strip()
            # 3) 退回剥标签
            if not name: name=clean(inner)
            name=clean(name)
            if not name or len(name)>60: continue
            seen.add(vid)
            # 真海报: 跳过 placeholder/logo 占位图, 取真 cover(可能相对路径)
            # 封面: dushe 真图被 cdndefend 锁加载不到 -> 用片名占位图(中文清晰, 比 App 大首字好看)
            pic=self._titimg(name)
            # 状态: v-item-bottom span 或 note/remarks
            rm=re.search(r'v-item-bottom[^>]*>\s*<span>\s*([^<]+?)\s*</span>',inner,re.S) \
               or re.search(r'class="[^"]*(?:note|remarks|score|msg)[^"]*"[^>]*>\s*([^<]{1,20})',inner)
            out.append({"vod_id":vid,"vod_name":name,"vod_pic":pic,"vod_remarks":clean(rm.group(1)) if rm else ""})
        return out   # 不在这查TMDB; 由各入口对"当前这一小批"查, 避免整页等很久

    PAGE=24   # 分类每App页条数(站点页~48条拆成2个App页; 越小出得快但要多拉几次, 越大拉得少但等久点)
    HOME=36   # 首页"推荐"显示条数(不分页, 一次性, 给多点)
    def homeContent(self,filter):
        h=self._get("/")
        # 频道分类: /channel/{id}.html + menu-item-label(电影/连续剧/动漫/综艺纪录/短剧)
        cls=[]; seen=set()
        for m in re.finditer(r'href="/channel/(\d+)\.html"[^>]*>.*?menu-item-label">\s*([^<]+?)\s*</div>',h,re.S):
            cid,cname=m.group(1),m.group(2).strip()
            if cid in seen or not cname: continue
            seen.add(cid); cls.append({"type_id":cid,"type_name":cname})
        if not cls:  # 兜底: 站点改版时用已知频道
            cls=[{"type_id":"1","type_name":"电影"},{"type_id":"2","type_name":"连续剧"},
                 {"type_id":"3","type_name":"动漫"},{"type_id":"4","type_name":"综艺纪录"},{"type_id":"6","type_name":"短剧"}]
        lst=self._cards(h)[:self.HOME]; self._fill_poster(lst)   # 推荐给 HOME 条
        return {"class":cls[:12],"list":lst}
    def homeVideoContent(self):
        lst=self._cards(self._get("/"))[:self.HOME]; self._fill_poster(lst)
        return {"list":lst}
    def categoryContent(self,tid,pg,filter,extend):
        page=int(pg) if str(pg).isdigit() else 1
        # 站点页~48条 拆成每App页 PAGE 条: 边滚边出海报, 每页只查少量TMDB(站点页结果缓存, 不重复抓)
        per=max(1,48//self.PAGE); site_pg=(page-1)//per+1; off=((page-1)%per)*self.PAGE
        ck=(tid,site_pg); cards=self._catcache.get(ck)
        if cards is None:
            path="/channel/%s.html"%tid if site_pg<=1 else "/channel/%s.html?page=%d"%(tid,site_pg)
            cards=self._cards(self._get(path)); self._catcache[ck]=cards
        sub=cards[off:off+self.PAGE]; self._fill_poster(sub)
        more=(off+self.PAGE<len(cards)) or len(cards)>=40   # 本站点页还有 或 满页(估计有下一站点页)
        return {"list":sub,"page":page,"pagecount":(page+1 if more else page),"limit":len(sub),"total":999999}
    def searchContent(self,key,quick,pg="1"):
        if not self.token: self._warm()
        h=self._get(f"/search?k={quote(key)}&t={quote(self.token,safe='')}")
        lst=self._cards(h); self._fill_poster(lst)
        return {"list":lst,"page":1,"pagecount":1,"limit":30,"total":0}

    def _line_rank(self,nm):
        """静态线路排序分(越小越前)。实测3部大秦帝国定的: 超清/4K加密不能播沉底;
        线路名/副标签自带速度等级 -> 国内加速>播放快>香港加速>高清蓝光>720P>标清。想调顺序改这里即可。"""
        # 116服务器实测6部剧(大秦帝国×3 + 王保长×3)定的, 替代站点标签(站点"中国大陆加速"实测反而最慢)
        if re.search(r'超清|4K',nm): return 95               # 加密取不到地址, 不能播 -> 最后
        if '中国大陆' in nm: return 85                        # ❗两组都1KB/s 最慢(HN), 站点"加速"标签骗人 -> 沉底
        if re.search(r'720',nm) or re.match(r'WJ线路',nm): return 70   # 实测慢(6~51)
        if re.match(r'蓝光3(?!\d)',nm): return 5             # ★实测最快最稳(204/288) + 你确认 -> 置顶
        if re.match(r'IK线路|蓝光9',nm): return 15            # 实测快(215/216)
        if re.match(r'LZ线路',nm): return 20                 # 实测很快(117/474), 但标清画质
        if re.match(r'蓝光1(?!\d)',nm): return 28            # 实测中(104~187)
        if re.match(r'FF线路',nm): return 45                 # 实测忽快忽慢(69~566), 你说不快 -> 中
        if '蓝光' in nm or '高清' in nm: return 30            # 其它蓝光/高清(实测中)
        return 50                                            # 未测到(SB/XL/GS/JY/蓝光7等) -> 中

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
        # 导演/演员/备注: detail-info-row(side=标签, main=值)
        info={}
        for mm in re.finditer(r'detail-info-row-side">\s*([^<:：]+)[:：]?\s*</div>\s*<div class="detail-info-row-main">(.*?)</div>',h,re.S):
            side=mm.group(1).strip()
            val=re.sub(r'<[^>]+>',' ',mm.group(2)); val=re.sub(r'\s+',' ',val).strip()
            if val and side not in info: info[side]=val
        director=info.get("导演","")
        actor=info.get("演员",info.get("主演",""))
        remarks=info.get("备注","")
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
        lines=[]; used=set()
        for n,(sid,eps) in enumerate(routes.items()):
            raw=labels[n].strip() if n<len(labels) and labels[n].strip() else "线路%d"%(n+1)
            sub=subs[n].strip() if n<len(subs) and subs[n].strip() else ""
            nm=("%s(%s)"%(raw,sub)) if sub else raw   # 例: 蓝光(高清)、FF线路(播放快/高清)、WJ线路(720P)
            nm=re.sub(r'[\$#]',' ',nm).strip()
            base=nm; k=2
            while nm in used: nm=base+str(k); k+=1   # 防重名被 App 合并
            used.add(nm)
            epstr="#".join(lab.replace("#","＃").replace("$","￥")+"$"+href for href,lab in eps)
            lines.append((self._line_rank(nm),nm,epstr))   # 静态排序分(基于线路名/副标签的质量等级)
        # 静态排序(0延迟, 无探测): 国内加速/播放快/香港加速 在前, 标清/720P 靠后, 超清4K(加密)沉底
        lines.sort(key=lambda l:l[0])   # 稳定排序, 同档保持原序
        pf=[l[1] for l in lines]; pu=[l[2] for l in lines]
        # 封面留空 -> App 才会去取 TMDB 剧照填卡片(带 logo 封面会压制 TMDB, 全是 logo)
        cont=("[%s] %s"%(VER,desc)).strip()  # 简介开头放版本标记 + 真实剧情
        return {"list":[{"vod_id":vid,"vod_name":name,"vod_pic":"","vod_year":year,
                         "vod_director":director,"vod_actor":actor,"vod_remarks":remarks,
                         "vod_content":cont,
                         "vod_play_from":"$$$".join(pf) if pf else "毒舌",
                         "vod_play_url":"$$$".join(pu)}]}

    def _extract_url(self,h):
        """从 /play/ 页 HTML 取 player_aaaa.url (m3u8/mp4 明文地址), 取不到返回 ''。"""
        url=""
        pm=re.search(r'player_aaaa\s*=\s*(\{.*?\})\s*</script>',h,re.S) or re.search(r'player_aaaa\s*=\s*(\{.*?\});',h,re.S)
        if pm:
            try: url=json.loads(pm.group(1)).get("url","")
            except Exception: url=""
        if not url:
            um=re.search(r'"url"\s*:\s*"([^"]+?\.(?:m3u8|mp4)[^"]*)"',h) or re.search(r'(https?:[^"\'\\]+?\.(?:m3u8|mp4)[^"\'\\]*)',h)
            if um: url=um.group(1)
        return (url or "").replace("\\/","/")
    # ---- 线路真实测速(参考 iptv_check): 抓首集 m3u8 -> 测首切片速度, 快的排前 ----
    def _tget(self,path,timeout=4):
        """线程安全抓取(用 requests.get + 显式 cdndefend cookie, 不动 self.session)。"""
        url=self.host+path if path.startswith("/") else path
        hdr={"User-Agent":self.ua,"Referer":self.host+"/","Accept-Language":"zh-CN,zh;q=0.9"}
        if self.cdcookie: hdr["Cookie"]=self.cdcookie
        try:
            r=requests.get(url,headers=hdr,timeout=timeout,verify=False); r.encoding="utf-8"; return r.text
        except Exception: return ""
    def _first_seg(self,text,base):
        for ln in (text or "").splitlines():
            ln=ln.strip()
            if ln and not ln.startswith("#"): return urljoin(base,ln)
        return ""
    def _line_score(self,first_href):
        """探一条线路第一集。分层: 真测速(≤4e6,越快越小) < 能取址但抓不到流/被挑战(5e6) < 确认加密(9e6)。"""
        import time as _t
        h=self._tget(first_href,3)
        if not h: return 5e6                           # 抓取失败 -> 中间(不误判死)
        if "verifying your browser" in h[:400] or "cdndefend" in h[:200]: return 5e6  # 被cdndefend挑战 -> 中间
        url=self._extract_url(h)
        if not url: return 9e6                         # 真有内容但取不到地址 -> 确认加密, 排最后
        hdr={"User-Agent":self.ua,"Referer":self.host+"/"}
        try:
            low=url.split("?")[0].lower()
            if low.endswith(".mp4"):
                seg=url
            else:
                r=requests.get(url,headers=hdr,timeout=3,verify=False)
                if r.status_code>=400: return 5e6      # 抓不到流(可能探测受限) -> 中间, 不误判死
                seg=self._first_seg(r.text,url)
                if not seg: return 5e6
                if seg.split("?")[0].lower().endswith(".m3u8"): return 5e6   # 主playlist不下钻 -> 中间
            ts=_t.time()
            rr=requests.get(seg,headers=dict(hdr,Range="bytes=0-65535"),timeout=3,verify=False,stream=True)
            data=rr.raw.read(65536); dt=_t.time()-ts
            if rr.status_code>=400 or len(data)<4096: return 5e6
            kbps=(len(data)/1024.0)/max(dt,0.01)
            return min(100000.0/max(kbps,0.1), 4e6)    # 真实速度分(越快越小), 封顶 4e6 < 中间层
        except Exception:
            return 5e6
    def _probe_lines(self,items,need=3,deadline=5):
        """全并发测速; 收到 need 条"真实快线路"或到 deadline 就停(不等慢的)。返回 {name: score}。"""
        import time as _t
        scores={}
        def work(name,href):
            try: scores[name]=self._line_score(href)
            except Exception: scores[name]=5e6
        try:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            ex=ThreadPoolExecutor(max_workers=min(6,max(1,len(items))))   # 并发别太高, 否则 cdndefend 挑战增多
            futs=[ex.submit(work,n,h) for n,h in items]
            end=_t.time()+deadline
            for _f in as_completed(futs,timeout=deadline+1):
                if sum(1 for v in scores.values() if v<=4e6)>=need: break  # 够几条快的就停
                if _t.time()>end: break
            ex.shutdown(wait=False)
        except Exception: pass
        return scores

    def playerContent(self,flag,id,vipFlags):
        url=self._extract_url(self._get(id,self.host+"/"))
        if not url: return {"parse":1,"jx":0,"url":""}
        return {"parse":0,"jx":0,"url":url,"header":{"User-Agent":self.ua,"Referer":self.host+"/"}}
