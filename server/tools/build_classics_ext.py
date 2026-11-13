"""生成 app/data/classics_ext.u16d.xz（引文核对用的古籍句库，见 app/famous.py source_parts）：古籍里每个 4–12 字小句的 CRC32 哈希
（简体与繁体两种写法），排好序后存相邻差值（uint16），xz 压缩。只保存哈希，不保存原文。
用法：python tools/build_classics_ext.py 输出.xz 文件列表1.txt [文件列表2.txt …]（列表里每行一个古籍 .txt 路径）
2026-11-13 所用书目（约 2.3 亿字，2083 万个哈希）：
- 殆知阁古籍文献 garychowcmu/daizhigev20：
  史藏/正史 二十四史；史藏/编年 资治通鉴、续资治通鉴；
  儒藏 论语、孟子、大学、中庸、四书章句集注、尚书、诗经、礼记、周礼、仪礼、春秋三传、孝经、尔雅、传习录、朱子语类、近思录、
  二程遗书、二程粹言、张子全书、周子全书、呻吟语、颜氏家训、了凡四训、围炉夜话、朱子家训、菜根谭、三字经、千字文、增广贤文、
  弟子规、幼学琼林、格言联璧、孔子家语、中说、扬子法言、新书、家范、帝范、太极图说、北溪字义、困知记、居业录；
  子藏 诸子、兵家、法家、农家（全部）；
  集藏/文总集 全唐文、全上古三代秦汉三国六朝文、文选、唐宋八大家文钞、御选古文渊鉴、宋文鉴、唐文粹、元文类、古文辞类纂、骈体文钞、
  文章正宗、明文衡；集藏/文评（全部）；
  集藏/四库别集与子藏/笔记中的名家别集、笔记 74 种（韩柳欧苏王曾、陆游、王阳明、朱熹、范仲淹、李杜白元、陶渊明……梦溪笔谈、
  容斋随笔、日知录、阅微草堂笔记等）；
  佛藏 金刚经、心经、法华经、华严经（八十卷）、普贤行愿品、楞严经、楞伽经、维摩诘经、阿弥陀经、无量寿经、药师经、地藏经、
  大般涅槃经、四十二章经、百喻经、六祖坛经、景德传灯录、五灯会元、碧岩录；
  道藏 太上感应篇、黄帝阴符经、冲虚至德真经、南华真经、周易参同契、悟真篇。
- NiuTrans/Classical-Modern 古文原文（全部）。"""
import lzma, re, sys, zlib
import numpy as np
from opencc import OpenCC
t2s, s2t = OpenCC("t2s"), OpenCC("s2t")
SPLIT = re.compile(r"[，。！？；：、,.!?;:\s「」『』“”‘’（）()《》〈〉—…·]+")
hs = set()
for lst in sys.argv[2:]:
    for f in open(lst, encoding="utf-8").read().splitlines():
        if not f.strip():
            continue
        s = t2s.convert(open(f, encoding="utf-8", errors="ignore").read())
        for txt in (s, s2t.convert(s)):
            for frag in SPLIT.split(txt):
                g = re.sub(r"[^一-鿿]", "", frag)
                if 4 <= len(g) <= 12:
                    hs.add(zlib.crc32(g.encode()))
h = np.array(sorted(hs), dtype=np.uint32)
d = np.diff(h, prepend=np.uint32(0)); assert d.max() < 65536
open(sys.argv[1], "wb").write(lzma.compress(d.astype("<u2").tobytes(), preset=9 | lzma.PRESET_EXTREME))
print("hashes", len(h))
