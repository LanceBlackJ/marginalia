"""顶刊顶会白名单：随机探索只会推荐发表在这些来源上的论文。

设计原则：
- 名单是一份可审阅的常量（下面的 `_VENUES`），每项带分类；想增删来源只改这里。
- 匹配只认“来源名称归一化后与白名单别名完全相等”，绝不做子串/模糊匹配：
  workshop、companion、预印本服务器（arXiv 等）、同名缩写但不是主会的，都不会被放行。
- 来源名称取不到、来源类型是 repository/metadata 等，一律不放行（“只显示来源可确认的论文”）。
- 检索时直接按来源过滤（OpenAlex 的 source id），而不是先随机再丢弃；source id 通过 /sources 搜索并核对名称得到，
  结果缓存在本地库里，不手写 id（手写容易错且无法核对）。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# OpenAlex 一级学科 id（见 recommend.FIELDS）
CS, BIO, BIOCHEM, CHEM, EARTH, ECON, ENG, IMMUNO, MATERIALS, MATH, MED, NEURO, PHYS, PSY, ENERGY = (
    17, 11, 13, 16, 19, 20, 22, 24, 25, 26, 27, 28, 31, 32, 21)


@dataclass(frozen=True)
class Venue:
    id: str
    name: str
    short: str
    kind: str  # journal | conference
    category: str
    fields: tuple[int, ...] | None  # None：综合性来源，适用于所有学科
    aliases: tuple[str, ...]
    search: str  # 在 OpenAlex /sources 里检索用的名称
    level: str = "top"  # top：顶刊顶会（随机探索只用这一档）| core：各领域的重要期刊/会议（只用于来源页面和介绍）
    intro: str | None = None  # core 项自带的简介（top 项的简介在 venue_info.INTRO）

    @property
    def search_terms(self) -> tuple[str, ...]:
        """在 OpenAlex /sources 里依次检索的名称：先用 search，找不到再试别名（最多 4 个，避免无谓请求）。"""
        out: list[str] = []
        for t in (self.search, *self.aliases):
            if t and t not in out and len(out) < 4:
                out.append(t)
        return tuple(out)

    @property
    def label(self) -> str:
        if self.level == "core":
            return "已确认重要会议" if self.kind == "conference" else "已确认重要期刊"
        return "已确认顶会" if self.kind == "conference" else "已确认顶刊"


def _v(id_: str, name: str, short: str, kind: str, category: str, fields, *aliases: str, search: str | None = None) -> Venue:
    return Venue(id_, name, short, kind, category, tuple(fields) if fields else None, (name, short, *aliases), search or name)


_VENUES: tuple[Venue, ...] = (
    # ---------------- 综合 / 自然科学顶刊 ----------------
    _v("nature", "Nature", "Nature", "journal", "综合顶刊", None),
    _v("science", "Science", "Science", "journal", "综合顶刊", None),
    _v("cell", "Cell", "Cell", "journal", "综合顶刊", (BIOCHEM, BIO, MED, IMMUNO)),
    _v("pnas", "Proceedings of the National Academy of Sciences", "PNAS", "journal", "综合顶刊", None,
       "Proceedings of the National Academy of Sciences of the United States of America"),
    _v("nat-mach-intell", "Nature Machine Intelligence", "Nat. Mach. Intell.", "journal", "Nature 系列", (CS, ENG)),
    _v("nat-comput-sci", "Nature Computational Science", "Nat. Comput. Sci.", "journal", "Nature 系列", (CS, MATH, PHYS)),
    _v("nat-methods", "Nature Methods", "Nat. Methods", "journal", "Nature 系列", (BIOCHEM, BIO)),
    _v("nat-biotech", "Nature Biotechnology", "Nat. Biotechnol.", "journal", "Nature 系列", (BIOCHEM, BIO)),
    _v("nat-med", "Nature Medicine", "Nat. Med.", "journal", "Nature 系列", (MED,)),
    _v("nat-genet", "Nature Genetics", "Nat. Genet.", "journal", "Nature 系列", (BIOCHEM, BIO, MED)),
    _v("nat-neuro", "Nature Neuroscience", "Nat. Neurosci.", "journal", "Nature 系列", (NEURO, PSY)),
    _v("nat-immunol", "Nature Immunology", "Nat. Immunol.", "journal", "Nature 系列", (IMMUNO, MED)),
    _v("nat-phys", "Nature Physics", "Nat. Phys.", "journal", "Nature 系列", (PHYS,)),
    _v("nat-chem", "Nature Chemistry", "Nat. Chem.", "journal", "Nature 系列", (CHEM,)),
    _v("nat-mater", "Nature Materials", "Nat. Mater.", "journal", "Nature 系列", (MATERIALS, PHYS, CHEM)),
    _v("nat-nano", "Nature Nanotechnology", "Nat. Nanotechnol.", "journal", "Nature 系列", (MATERIALS, PHYS, CHEM)),
    _v("nat-energy", "Nature Energy", "Nat. Energy", "journal", "Nature 系列", (ENERGY, ENG)),
    _v("nat-climate", "Nature Climate Change", "Nat. Clim. Change", "journal", "Nature 系列", (EARTH, ENERGY)),
    _v("nat-geosci", "Nature Geoscience", "Nat. Geosci.", "journal", "Nature 系列", (EARTH,)),
    _v("nat-astron", "Nature Astronomy", "Nat. Astron.", "journal", "Nature 系列", (PHYS,)),
    _v("nat-electron", "Nature Electronics", "Nat. Electron.", "journal", "Nature 系列", (ENG, PHYS)),
    _v("nat-human-behav", "Nature Human Behaviour", "Nat. Hum. Behav.", "journal", "Nature 系列", (PSY, 33)),
    _v("sci-robotics", "Science Robotics", "Sci. Robot.", "journal", "Science 系列", (ENG, CS)),
    _v("sci-transl-med", "Science Translational Medicine", "Sci. Transl. Med.", "journal", "Science 系列", (MED, BIOCHEM)),
    _v("sci-immunol", "Science Immunology", "Sci. Immunol.", "journal", "Science 系列", (IMMUNO, MED)),
    _v("neuron", "Neuron", "Neuron", "journal", "Cell 系列", (NEURO,)),
    _v("mol-cell", "Molecular Cell", "Mol. Cell", "journal", "Cell 系列", (BIOCHEM, BIO)),
    _v("cancer-cell", "Cancer Cell", "Cancer Cell", "journal", "Cell 系列", (MED, BIOCHEM)),
    _v("immunity", "Immunity", "Immunity", "journal", "Cell 系列", (IMMUNO, MED)),
    _v("lancet", "The Lancet", "Lancet", "journal", "医学顶刊", (MED,)),
    _v("nejm", "The New England Journal of Medicine", "NEJM", "journal", "医学顶刊", (MED,), "New England Journal of Medicine"),
    _v("jama", "JAMA", "JAMA", "journal", "医学顶刊", (MED,), "JAMA: The Journal of the American Medical Association"),
    _v("bmj", "BMJ", "BMJ", "journal", "医学顶刊", (MED,), "BMJ: British Medical Journal"),
    _v("prl", "Physical Review Letters", "PRL", "journal", "物理顶刊", (PHYS,)),
    _v("rmp", "Reviews of Modern Physics", "RMP", "journal", "物理顶刊", (PHYS,)),
    _v("jacs", "Journal of the American Chemical Society", "JACS", "journal", "化学顶刊", (CHEM,)),
    _v("angew", "Angewandte Chemie International Edition", "Angew. Chem.", "journal", "化学顶刊", (CHEM,), "Angewandte Chemie"),
    _v("aer", "American Economic Review", "AER", "journal", "经济学顶刊", (ECON,)),
    _v("qje", "The Quarterly Journal of Economics", "QJE", "journal", "经济学顶刊", (ECON,)),
    _v("econometrica", "Econometrica", "Econometrica", "journal", "经济学顶刊", (ECON,)),
    _v("jpe", "Journal of Political Economy", "JPE", "journal", "经济学顶刊", (ECON,)),
    _v("annals-math", "Annals of Mathematics", "Ann. Math.", "journal", "数学顶刊", (MATH,)),
    _v("jams", "Journal of the American Mathematical Society", "JAMS", "journal", "数学顶刊", (MATH,)),
    _v("inventiones", "Inventiones Mathematicae", "Invent. Math.", "journal", "数学顶刊", (MATH,)),
    # ---------------- 计算机顶刊 ----------------
    _v("jmlr", "Journal of Machine Learning Research", "JMLR", "journal", "计算机顶刊", (CS, MATH)),
    _v("tpami", "IEEE Transactions on Pattern Analysis and Machine Intelligence", "TPAMI", "journal", "计算机顶刊", (CS, ENG)),
    _v("ijcv", "International Journal of Computer Vision", "IJCV", "journal", "计算机顶刊", (CS,)),
    _v("tkde", "IEEE Transactions on Knowledge and Data Engineering", "TKDE", "journal", "计算机顶刊", (CS,)),
    _v("jacm", "Journal of the ACM", "JACM", "journal", "计算机顶刊", (CS, MATH)),
    _v("aij", "Artificial Intelligence", "AIJ", "journal", "计算机顶刊", (CS,)),
    _v("proc-ieee", "Proceedings of the IEEE", "Proc. IEEE", "journal", "计算机顶刊", (CS, ENG)),
    _v("pvldb", "Proceedings of the VLDB Endowment", "PVLDB", "journal", "计算机顶刊", (CS,), "VLDB", "Very Large Data Bases"),
    _v("tocl", "Transactions of the Association for Computational Linguistics", "TACL", "journal", "计算机顶刊", (CS,)),
    # ---------------- 计算机顶会：机器学习 / AI ----------------
    _v("neurips", "Neural Information Processing Systems", "NeurIPS", "conference", "机器学习/AI 顶会", (CS,),
       "Advances in Neural Information Processing Systems", "NIPS", "Conference on Neural Information Processing Systems"),
    _v("icml", "International Conference on Machine Learning", "ICML", "conference", "机器学习/AI 顶会", (CS,)),
    _v("iclr", "International Conference on Learning Representations", "ICLR", "conference", "机器学习/AI 顶会", (CS,)),
    _v("aaai", "AAAI Conference on Artificial Intelligence", "AAAI", "conference", "机器学习/AI 顶会", (CS,),
       "Proceedings of the AAAI Conference on Artificial Intelligence", "National Conference on Artificial Intelligence"),
    _v("ijcai", "International Joint Conference on Artificial Intelligence", "IJCAI", "conference", "机器学习/AI 顶会", (CS,),
       "International Joint Conferences on Artificial Intelligence Organization"),
    _v("colt", "Conference on Learning Theory", "COLT", "conference", "机器学习/AI 顶会", (CS, MATH)),
    _v("uai", "Conference on Uncertainty in Artificial Intelligence", "UAI", "conference", "机器学习/AI 顶会", (CS, MATH),
       "Uncertainty in Artificial Intelligence"),
    # ---------------- 计算机视觉 ----------------
    _v("cvpr", "IEEE/CVF Conference on Computer Vision and Pattern Recognition", "CVPR", "conference", "视觉顶会", (CS,),
       "IEEE Conference on Computer Vision and Pattern Recognition", "Computer Vision and Pattern Recognition",
       # OpenAlex 里主会的真实来源名（重复的 “/Proceedings” 是它自己的命名）；Workshops（CVPRW）不是主会，不在别名里
       "IEEE Computer Society Conference on Computer Vision and Pattern Recognition/Proceedings - IEEE Computer Society Conference on Computer Vision and Pattern Recognition/Proceedings"),
    _v("iccv", "IEEE/CVF International Conference on Computer Vision", "ICCV", "conference", "视觉顶会", (CS,),
       "IEEE International Conference on Computer Vision", "International Conference on Computer Vision"),
    _v("eccv", "European Conference on Computer Vision", "ECCV", "conference", "视觉顶会", (CS,)),
    # ---------------- 自然语言处理 ----------------
    _v("acl", "Annual Meeting of the Association for Computational Linguistics", "ACL", "conference", "NLP 顶会", (CS,),
       "Meeting of the Association for Computational Linguistics", "Association for Computational Linguistics",
       "Proceedings of the conference - Association for Computational Linguistics. Meeting"),
    _v("emnlp", "Conference on Empirical Methods in Natural Language Processing", "EMNLP", "conference", "NLP 顶会", (CS,),
       "Empirical Methods in Natural Language Processing"),
    _v("naacl", "North American Chapter of the Association for Computational Linguistics", "NAACL", "conference", "NLP 顶会", (CS,),
       "Conference of the North American Chapter of the Association for Computational Linguistics: Human Language Technologies"),
    # ---------------- 数据挖掘 / 数据库 / 信息检索 / Web ----------------
    _v("kdd", "ACM SIGKDD International Conference on Knowledge Discovery and Data Mining", "KDD", "conference", "数据/Web 顶会", (CS,),
       "Knowledge Discovery and Data Mining", "International Conference on Knowledge Discovery and Data Mining",
       "ACM SIGKDD Conference on Knowledge Discovery and Data Mining"),
    _v("www", "The Web Conference", "WWW", "conference", "数据/Web 顶会", (CS,),
       "International World Wide Web Conference", "International Conference on World Wide Web", "ACM Web Conference"),
    _v("sigir", "International ACM SIGIR Conference on Research and Development in Information Retrieval", "SIGIR", "conference",
       "数据/Web 顶会", (CS,), "Annual International ACM SIGIR Conference on Research and Development in Information Retrieval",
       "Research and Development in Information Retrieval"),
    _v("sigmod", "ACM SIGMOD International Conference on Management of Data", "SIGMOD", "conference", "数据/Web 顶会", (CS,),
       "International Conference on Management of Data", "Proceedings of the ACM on Management of Data"),
    _v("icde", "IEEE International Conference on Data Engineering", "ICDE", "conference", "数据/Web 顶会", (CS,),
       "International Conference on Data Engineering"),
    # ---------------- 系统 / 网络 / 安全 ----------------
    _v("osdi", "USENIX Symposium on Operating Systems Design and Implementation", "OSDI", "conference", "系统/安全顶会", (CS,),
       "Operating Systems Design and Implementation"),
    _v("sosp", "ACM Symposium on Operating Systems Principles", "SOSP", "conference", "系统/安全顶会", (CS,),
       "Symposium on Operating Systems Principles", "ACM SIGOPS Symposium on Operating Systems Principles"),
    _v("nsdi", "USENIX Symposium on Networked Systems Design and Implementation", "NSDI", "conference", "系统/安全顶会", (CS,),
       "Networked Systems Design and Implementation"),
    _v("sigcomm", "ACM SIGCOMM Conference", "SIGCOMM", "conference", "系统/安全顶会", (CS,), "Conference on Applications, Technologies, Architectures, and Protocols for Computer Communication"),
    _v("usenix-sec", "USENIX Security Symposium", "USENIX Security", "conference", "系统/安全顶会", (CS,)),
    _v("ccs", "ACM Conference on Computer and Communications Security", "CCS", "conference", "系统/安全顶会", (CS,),
       "Computer and Communications Security", "ACM SIGSAC Conference on Computer and Communications Security"),
    _v("sp", "IEEE Symposium on Security and Privacy", "S&P", "conference", "系统/安全顶会", (CS,), "Symposium on Security and Privacy"),
    _v("isca", "International Symposium on Computer Architecture", "ISCA", "conference", "系统/安全顶会", (CS, ENG),
       "ACM/IEEE International Symposium on Computer Architecture",
       "Proceedings - International Symposium on Computer Architecture/Proceedings"),
    _v("micro", "IEEE/ACM International Symposium on Microarchitecture", "MICRO", "conference", "系统/安全顶会", (CS, ENG),
       "International Symposium on Microarchitecture"),
    # ---------------- 理论 / 软件工程 / 图形 / 人机交互 ----------------
    _v("stoc", "ACM Symposium on Theory of Computing", "STOC", "conference", "理论顶会", (CS, MATH), "Symposium on Theory of Computing"),
    _v("focs", "IEEE Symposium on Foundations of Computer Science", "FOCS", "conference", "理论顶会", (CS, MATH),
       "Foundations of Computer Science", "Annual Symposium on Foundations of Computer Science"),
    _v("soda", "ACM-SIAM Symposium on Discrete Algorithms", "SODA", "conference", "理论顶会", (CS, MATH),
       "Symposium on Discrete Algorithms"),
    _v("icse", "International Conference on Software Engineering", "ICSE", "conference", "软件/图形/交互顶会", (CS,),
       "IEEE/ACM International Conference on Software Engineering"),
    _v("pldi", "ACM SIGPLAN Conference on Programming Language Design and Implementation", "PLDI", "conference", "软件/图形/交互顶会", (CS,),
       "Programming Language Design and Implementation"),
    _v("popl", "ACM SIGPLAN-SIGACT Symposium on Principles of Programming Languages", "POPL", "conference", "软件/图形/交互顶会", (CS,),
       "Principles of Programming Languages"),
    _v("siggraph", "ACM SIGGRAPH Conference", "SIGGRAPH", "conference", "软件/图形/交互顶会", (CS,), "ACM Transactions on Graphics"),
    _v("chi", "ACM CHI Conference on Human Factors in Computing Systems", "CHI", "conference", "软件/图形/交互顶会", (CS, PSY),
       "Conference on Human Factors in Computing Systems", "Human Factors in Computing Systems",
       "CHI Conference on Human Factors in Computing Systems"),
    _v("uist", "ACM Symposium on User Interface Software and Technology", "UIST", "conference", "软件/图形/交互顶会", (CS,),
       "User Interface Software and Technology"),
)

def _core_venues() -> tuple[Venue, ...]:
    from .venues_core import CORE

    out = []
    for id_, name, short, kind, category, fields, aliases, intro in CORE:
        out.append(Venue(id_, name, short, kind, category, tuple(fields) if fields else None, (name, short, *aliases), name, "core", intro))
    return tuple(out)


def _more_venues() -> tuple[Venue, ...]:
    from .venues_more import _IEEE, _OTHER

    out = []
    for id_, name, short, category, fields, zh, focus in _IEEE:
        org = "IEEE 与 ACM" if name.startswith("IEEE/ACM") else "IEEE"
        kind_zh = "期刊" if ("Journal" in name or "Magazine" in name) else "汇刊"
        out.append(Venue(id_, name, short, "journal", category, tuple(fields), (name, short), name, "core", f"{org} 的{zh}{kind_zh}，{focus}方向。"))
    for id_, name, short, kind, category, fields, intro in _OTHER:
        out.append(Venue(id_, name, short, kind, category, tuple(fields), (name, short), name, "core", intro))
    return tuple(out)


TOP_VENUES: tuple[Venue, ...] = _VENUES
CORE_VENUES: tuple[Venue, ...] = _core_venues() + _more_venues()
VENUES: tuple[Venue, ...] = TOP_VENUES + CORE_VENUES  # 页面、介绍、名录用全部；随机探索只用 TOP_VENUES
BY_ID: dict[str, Venue] = {v.id: v for v in VENUES}

# ---------------- 归一化与匹配 ----------------
_PAREN = re.compile(r"\([^)]*\)|\[[^\]]*\]")
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
_ORDINAL = re.compile(r"\b\d+(?:st|nd|rd|th)\b")
_PREFIX = re.compile(r"^(?:proceedings of the|proceedings of|proceedings|the)\s+")
# 出现这些词的来源一律不放行：workshop、伴随会议、教程、海报、demo、预印本服务器、findings 等
_REJECT = re.compile(r"\b(?:workshops?|companion|adjunct|tutorials?|posters?|demos?|demonstrations?|findings|extended abstracts?|"
                     r"arxiv|preprints?|doctoral|student)\b", re.I)
_ALLOWED_TYPES = {"journal", "conference", "book series"}  # repository / metadata / ebook platform / other 不放行


def norm_venue(name: str | None) -> str:
    """来源名称归一化：小写、去括号内容、去年份与序数词、去 “Proceedings of the” 前缀、标点统一为空格。"""
    s = unicodedata.normalize("NFKC", name or "").lower().replace("&", " and ")
    s = _PAREN.sub(" ", s)
    s = _YEAR.sub(" ", s)
    s = _ORDINAL.sub(" ", s)
    s = re.sub(r"[^a-z0-9/]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    prev = None
    while prev != s:  # 前缀可能叠加（“proceedings of the the …”）
        prev, s = s, _PREFIX.sub("", s).strip()
    return s


# 期刊名常按 ISO 4 缩写写（“IEEE Trans. Netw. Sci. Eng.”）：展开成全称再和别名比，整体相等才算，不做部分匹配
_ISO = {
    "trans": "transactions", "j": "journal", "jour": "journal", "int": "international", "intl": "international", "conf": "conference",
    "proc": "proceedings", "netw": "network", "sci": "science", "eng": "engineering", "syst": "systems", "sys": "systems", "inf": "information",
    "technol": "technology", "learn": "learning", "intell": "intelligence", "mach": "machine", "anal": "analysis", "pattern": "pattern",
    "process": "processing", "commun": "communications", "comput": "computing", "appl": "applications", "res": "research", "rev": "reviews",
    "lett": "letters", "stat": "statistical", "phys": "physical", "math": "mathematics", "biomed": "biomedical", "med": "medical",
    "ind": "industrial", "autom": "automation", "robot": "robotics", "cybern": "cybernetics", "multimed": "multimedia", "wirel": "wireless",
    "mob": "mobile", "distrib": "distributed", "parallel": "parallel", "softw": "software", "knowl": "knowledge", "discov": "discovery",
    "manag": "management", "soc": "social", "env": "environmental", "energ": "energy", "mater": "materials", "chem": "chemical",
    "ann": "annals", "quart": "quarterly", "sens": "sensing", "imaging": "imaging", "graph": "graphics", "vis": "vision", "recognit": "recognition",
}
_STOP = {"and", "of", "on", "for", "the", "in", "a", "an", "to", "with"}


def norm_loose(name: str | None) -> str:
    """宽松归一化（只用于“完全相同”的比较）：展开 ISO 缩写词、去掉连接词、去词尾复数。"""
    toks = []
    for t in norm_venue(name).replace("/", " ").split():
        t = _ISO.get(t, t)
        if t in _STOP:
            continue
        toks.append(t[:6])  # computer/computing/computational、network/networks 这类只差词尾的写法归到一起
    return " ".join(toks) if len(toks) >= 3 else ""  # 太短的名称（缩写、单词）不做宽松比较，只认完全相等


_ALIAS_INDEX: dict[str, Venue] = {}
for _venue in VENUES:
    for _alias in _venue.aliases:
        _ALIAS_INDEX.setdefault(norm_venue(_alias), _venue)


_LOOSE_INDEX: dict[str, Venue] = {}
for _venue in VENUES:
    for _alias in _venue.aliases:
        if norm_loose(_alias):
            _LOOSE_INDEX.setdefault(norm_loose(_alias), _venue)


def match_venue(name: str | None, venue_type: str | None = None, *, loose: bool = True) -> Venue | None:
    """来源名称（venue/journal/container-title/OpenAlex source）是否是白名单里的顶刊顶会。取不到来源、类型不符、含 workshop 等一律返回 None。"""
    if not name or not name.strip():
        return None
    if venue_type is not None and venue_type not in _ALLOWED_TYPES:
        return None
    if _REJECT.search(name):
        return None
    hit = _ALIAS_INDEX.get(norm_venue(name))
    if hit is None and loose:  # 缩写写法：展开后整体相等（随机探索的“顶刊顶会确认”不用这一步，仍然只认完全相等的名称）
        key = norm_loose(name)
        hit = _LOOSE_INDEX.get(key) if key else None
    return hit


def venues_for_field(field_id: int | None) -> list[Venue]:
    """随机探索用：只有顶刊顶会。"""
    if field_id is None:
        return list(TOP_VENUES)
    return [v for v in TOP_VENUES if v.fields is None or field_id in v.fields]


def public_list() -> list[dict]:
    return [{"id": v.id, "name": v.name, "short": v.short, "kind": v.kind, "category": v.category, "level": v.level,
             "fields": list(v.fields) if v.fields else None} for v in VENUES]
