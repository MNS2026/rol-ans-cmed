# -*- coding: utf-8 -*-
"""
Atualiza a ferramenta Rol da ANS x CMED.

Roda todo dia no GitHub Actions:
  1. descobre e baixa a lista de precos CMED mais recente (Anvisa)
  2. descobre e baixa o Anexo II vigente do Rol (ANS)
  3. extrai as diretrizes, cruza com as substancias da CMED
  4. aplica a curadoria tecnica registrada em dados/curadoria.csv
  5. regera index.html apenas se algo mudou

Substancia nova no Rol nao entra sozinha: fica pendente de curadoria e o
robo avisa. O criterio continua humano.
"""
import csv, json, os, re, sys, unicodedata, collections, urllib.request, hashlib
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DADOS = RAIZ / "dados"
UA = {"User-Agent": "Mozilla/5.0 (compatible; rol-ans-cmed/1.0)"}

PAG_CMED = "https://www.gov.br/anvisa/pt-br/assuntos/medicamentos/cmed/precos"
PAG_ROL = ("https://www.gov.br/ans/pt-br/acesso-a-informacao/participacao-da-sociedade"
           "/atualizacao-do-rol-de-procedimentos")

def log(*a):
    print(*a, flush=True)

def baixa(url, destino=None, timeout=180):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        conteudo = r.read()
    if destino:
        Path(destino).write_bytes(conteudo)
    return conteudo

def pagina(url):
    html = baixa(url).decode("utf-8", "replace")
    return html.replace("\\u002F", "/").replace("\\/", "/")

# ----------------------------------------------------------------- descoberta
def descobre_cmed():
    """A pagina da CMED traz o link do arquivo vigente embutido no HTML.

    O nome do arquivo ja mudou uma vez (set/2026: xls_conformidade_site_* -> lista_pmc_*).
    Aceita os dois padroes e escolhe o mais recente pela data no nome, nao pela ordem alfabetica.
    A lista PMVG (lista_pmvg_*) e outra tabela e fica de fora de proposito."""
    padrao_arq = r"(?:lista_pmc|xls_conformidade_site)_(\d{8})_\d+\.xlsx"
    achados = {m.group(0): m.group(1) for m in re.finditer(padrao_arq, pagina(PAG_CMED))}
    if not achados:
        raise RuntimeError("nao encontrei a lista de precos na pagina da CMED "
                           "(a Anvisa pode ter mudado de novo o nome do arquivo)")
    arq = max(achados, key=lambda a: (achados[a], a))
    url = f"{PAG_CMED}/arquivos/{arq}/@@download/file"
    return arq, url

def descobre_rol():
    """O nome do PDF do Anexo II carrega a RN vigente: ..._RN676.2026.pdf"""
    html = pagina(PAG_ROL)
    urls = re.findall(r"https?://[^\"'<> ]*Anexo_II_DUT[^\"'<> ]*\.pdf", html)
    if not urls:
        raise RuntimeError("nao encontrei o Anexo II na pagina de atualizacao do Rol da ANS")
    url = sorted(set(urls))[-1]
    arq = url.rsplit("/", 1)[-1]
    m = re.search(r"_RN(\d{3})\.(\d{4})\.pdf$", arq)
    return arq, url, (f"{m.group(1)}/{m.group(2)}" if m else "desconhecida")

# ------------------------------------------------------------------ Anexo II
def texto_do_pdf(caminho):
    from pypdf import PdfReader
    return "\n".join((p.extract_text() or "") for p in PdfReader(str(caminho)).pages)

RUIDO = re.compile(r"^\s*(Publicada em .*|ANEXO II.*|PROCEDIMENTOS NA SA.*|"
                   r"DIRETRIZES DE UTILIZA.*|\d{1,3})\s*$")
TAB_HDR = re.compile(r"^\s*(SUBST[AÂ]NCIA|LOCALIZA[CÇ][AÃ]O|INDICA[CÇ][AÃ]O|MEDICAMENTO|"
                     r"VIA|POTENCIAL|GRAU|N[IÍ]VEL|CRIT[EÉ]RIO)\b")
ANOTACAO = re.compile(r"\((?:Inclu[ií]d[oa]|Alterad[oa]|Revogad[oa]|Reda[cç][aã]o|Retificad[oa])[^)]*\)?",
                      re.IGNORECASE)

def norm_map(texto):
    buf, mapa = [], []
    for i, ch in enumerate(texto):
        for d in unicodedata.normalize("NFKD", ch):
            if unicodedata.combining(d):
                continue
            for u in d.upper():
                buf.append(u)
                mapa.append(i)
    return "".join(buf), mapa

def norm(s):
    t, _ = norm_map(str(s))
    return re.sub(r"\s+", " ", t).strip()

def caixa_alta(texto, limite=0.85, minimo=6):
    limpo = ANOTACAO.sub(" ", texto)
    letras = [c for c in limpo if c.isalpha()]
    if len(letras) < minimo:
        return False
    return sum(1 for c in letras if c == c.upper()) / len(letras) > limite

def parse_anexo2(texto_bruto):
    linhas = texto_bruto.split("\n")
    sumario = [i for i, l in enumerate(linhas) if "...." in l]
    inicio = (max(sumario) + 1) if sumario else 0
    corpo = [l for l in linhas[inicio:] if not RUIDO.match(l.strip()) and l.strip()]

    def eh_titulo(l):
        m = re.match(r"^\s*(\d{1,3}(?:\.\d{1,2})?)\s*[-–.]?\s+(.{6,})$", l)
        if not m or not caixa_alta(m.group(2)):
            return None
        return m.group(1), m.group(2).strip()

    secoes, atual = [], None
    for l in corpo:
        t = eh_titulo(l)
        if t:
            if atual:
                secoes.append(atual)
            atual = {"num": t[0], "titulo": t[1], "linhas": [], "cont": True}
            continue
        if atual is None:
            continue
        if atual["cont"] and caixa_alta(l, 0.9, 2) \
           and not re.match(r"^\s*[a-z0-9]\s*[.)]", l.strip()) \
           and not TAB_HDR.match(l.strip()) and len(atual["titulo"]) < 150:
            atual["titulo"] += " " + l.strip()
        else:
            atual["cont"] = False
            atual["linhas"].append(l)
    if atual:
        secoes.append(atual)

    for s in secoes:
        s["titulo"] = re.sub(r"\s+", " ", s["titulo"]).strip()
        s["texto"] = f'{s["num"]}. {s["titulo"]}\n' + "\n".join(s["linhas"])
        s["rns"] = sorted(set(re.findall(r"RN\s*n?[ºo°]?\s*(\d{3})/(\d{4})", s["texto"])),
                          key=lambda x: (x[1], x[0]))
        s["norm"], s["mapa"] = norm_map(s["texto"])
    return secoes

# ---------------------------------------------------------------------- CMED
COL = {"SUB": 0, "LAB": 2, "GGREM": 3, "REG": 4, "PROD": 8, "APRES": 9, "CLASSE": 10,
       "TIPO": 11, "REGIME": 12, "PF": 13, "PMC": 39, "HOSP": 65, "TARJA": 72}
ALIQ = ["Sem impostos", "0%", "12%", "17%", "17,5%", "18%", "19%",
        "19,5%", "20%", "20,5%", "21%", "22%", "22,5%", "23%"]
PF_COLS = [13, 14, 15, 17, 19, 21, 23, 25, 27, 29, 31, 33, 35, 37]
PMC_COLS = [39, 40, 41, 43, 45, 47, 49, 51, 53, 55, 57, 59, 61, 63]

def num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None

def _chave_col(c):
    return re.sub(r"\s+", "", norm(c or ""))

def _mapeia_colunas(cab):
    """Localiza as colunas pelo NOME do cabecalho, nao pela posicao, e valida.
    Se a Anvisa mudar a planilha, o robo para com mensagem clara em vez de
    publicar precos na coluna errada."""
    global COL, PF_COLS, PMC_COLS
    pos = {}
    for i, c in enumerate(cab):
        pos.setdefault(_chave_col(c), i)
    def acha(*nomes, prefixo=False):
        for n in nomes:
            k = _chave_col(n)
            if k in pos:
                return pos[k]
            if prefixo:
                for kk, i in pos.items():
                    if kk.startswith(k):
                        return i
        raise RuntimeError(f"coluna '{nomes[0]}' nao encontrada na planilha da CMED")
    COL = {"SUB": acha("SUBSTÂNCIA"), "LAB": acha("LABORATÓRIO"), "GGREM": acha("CÓDIGO GGREM"),
           "REG": acha("REGISTRO"), "PROD": acha("PRODUTO"), "APRES": acha("APRESENTAÇÃO"),
           "CLASSE": acha("CLASSE TERAPÊUTICA"), "TIPO": acha("TIPO DE PRODUTO", prefixo=True),
           "REGIME": acha("REGIME DE PREÇO"), "HOSP": acha("RESTRIÇÃO HOSPITALAR"),
           "TARJA": acha("TARJA")}
    PF_COLS = [acha(f"PF {a}") for a in ALIQ]
    PMC_COLS = [acha(f"PMC {a}") for a in ALIQ]
    COL["PF"], COL["PMC"] = PF_COLS[0], PMC_COLS[0]

def carrega_cmed(caminho):
    """Procura em todas as abas a linha de cabecalho (1a celula = SUBSTANCIA) e a data
    de publicacao. Ate ago/2026 era uma aba so, cabecalho na linha 42; desde set/2026
    a data fica na aba 'Cabecalho' e os dados na aba 'Lista PMC', cabecalho na linha 1."""
    import openpyxl
    wb = openpyxl.load_workbook(caminho, read_only=True, data_only=True)
    pub, ws_dados, linha_cab = "", None, None
    for ws in wb.worksheets:
        for i, row in enumerate(ws.iter_rows(min_row=1, max_row=60, values_only=True), 1):
            for c in row:
                if not pub and c and "Publicada em" in str(c):
                    pub = str(c).strip()
            if ws_dados is None and row and row[0] and norm(row[0]) == "SUBSTANCIA":
                ws_dados, linha_cab = ws, i
    if ws_dados is None:
        raise RuntimeError("nao encontrei a linha de cabecalho (SUBSTANCIA) na planilha da CMED")
    it = ws_dados.iter_rows(min_row=linha_cab, values_only=True)
    _mapeia_colunas(next(it))
    idx = collections.defaultdict(list)
    for r in it:
        if not r or not r[COL["SUB"]]:
            continue
        for parte in str(r[COL["SUB"]]).split(";"):
            s = norm(parte)
            if len(s) >= 5:
                idx[s].append(r)
    wb.close()
    return idx, pub

# ---------------------------------------------------------------- cruzamento
def padrao(sub):
    return re.compile(r"(?<![A-Z0-9])" + r"\s+".join(re.escape(t) for t in sub.split() if t)
                      + r"(?![A-Z0-9])")

def cruza(secoes, cmed):
    res = {}
    for sub in cmed:
        pat = padrao(sub)
        ocs = []
        for s in secoes:
            for m in pat.finditer(s["norm"]):
                ini, fim = m.span()
                a = s["mapa"][max(0, ini - 200)]
                b = s["mapa"][min(len(s["mapa"]) - 1, fim + 200)]
                ocs.append({"dut": s["num"], "titulo": s["titulo"], "rns": s["rns"],
                            "trecho": "..." + re.sub(r"\s+", " ", s["texto"][a:b]).strip() + "..."})
        if ocs:
            res[sub] = ocs
    return res

# ----------------------------------------------------------------- curadoria
def le_curadoria():
    dec = {}
    with open(DADOS / "curadoria.csv", encoding="utf-8", newline="") as f:
        for linha in csv.DictReader(f, delimiter=";"):
            dec[linha["substancia"].strip()] = linha["decisao"].strip().upper()
    return dec

# -------------------------------------------------------------------- saidas
def monta_dados(achados, cmed, secoes, meta):
    meds = []
    for sub in sorted(achados):
        ocs, rows = achados[sub], cmed[sub]
        duts = sorted({o["dut"] for o in ocs}, key=lambda x: [int(p) for p in x.split(".")])
        diretrizes, vistos = [], set()
        for d in duts:
            o = next(x for x in ocs if x["dut"] == d)
            if o["titulo"] in vistos:
                continue
            vistos.add(o["titulo"])
            ev = []
            for x in ocs:
                if x["dut"] == d and x["trecho"] not in ev:
                    ev.append(x["trecho"])
            diretrizes.append({"n": d, "t": o["titulo"], "ev": ev[:3]})
        classes = collections.Counter(r[COL["CLASSE"]] for r in rows if r[COL["CLASSE"]])
        apres = sorted(
            [{"p": r[COL["PROD"]], "a": r[COL["APRES"]], "l": r[COL["LAB"]],
              "rg": r[COL["REGIME"]], "g": str(r[COL["GGREM"]] or ""),
              "h": str(r[COL["HOSP"]] or "").strip(), "tj": str(r[COL["TARJA"]] or "").strip(),
              "pf": [num(r[c]) for c in PF_COLS], "pmc": [num(r[c]) for c in PMC_COLS]}
             for r in rows], key=lambda a: (a["pf"][0] or 0))
        pf0 = [a["pf"][0] for a in apres if a["pf"][0]]
        meds.append({
            "s": sub, "tri": 0, "d": diretrizes,
            "rns": sorted({f"RN {a}/{b}" for o in ocs for a, b in o["rns"]},
                          key=lambda s: (s[-4:], s[3:6])),
            "c": classes.most_common(1)[0][0] if classes else "",
            "tp": sorted({r[COL["TIPO"]] for r in rows if r[COL["TIPO"]]}),
            "na": len(rows), "nl": len({a["l"] for a in apres}),
            "pfmin": min(pf0) if pf0 else None, "pfmax": max(pf0) if pf0 else None,
            "nhosp": sum(1 for a in apres if a["h"].lower().startswith("s")),
            "npmc": sum(1 for a in apres if a["pmc"][5]), "ap": apres,
        })
    return {"meta": meta, "aliq": ALIQ, "meds": meds}

def monta_html(dados):
    tpl = (RAIZ / "scripts" / "template.html").read_text(encoding="utf-8")
    titulo = re.search(r"<title>(.*?)</title>", tpl).group(1)
    corpo = tpl.replace(f"<title>{titulo}</title>\n", "").replace(
        "__DADOS__", json.dumps(dados, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/"))
    return f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<meta name="description" content="Medicamentos citados no Anexo II da RN 465/2021 (Rol da ANS) com os precos da tabela CMED/Anvisa.">
<meta name="color-scheme" content="light dark">
<title>{titulo}</title>
<style>
  *,*::before,*::after{{box-sizing:border-box}}
  html{{-webkit-text-size-adjust:100%}}
  body{{margin:0}}
  img,svg{{max-width:100%;height:auto}}
  button,input,select{{font:inherit;color:inherit}}
</style>
{corpo}
</body>
</html>
"""

def saida_workflow(chave, valor):
    destino = os.environ.get("GITHUB_OUTPUT")
    if destino:
        with open(destino, "a", encoding="utf-8") as f:
            f.write(f"{chave}={valor}\n")

# ---------------------------------------------------------------------- main
def main():
    estado_arq = DADOS / "estado.json"
    estado = json.loads(estado_arq.read_text(encoding="utf-8")) if estado_arq.exists() else {}

    arq_cmed, url_cmed = descobre_cmed()
    arq_rol, url_rol, rn = descobre_rol()
    log(f"CMED vigente : {arq_cmed}")
    log(f"Rol vigente  : {arq_rol}  (RN {rn})")

    mudou_cmed = estado.get("cmed_arquivo") != arq_cmed
    mudou_rol = estado.get("rol_arquivo") != arq_rol
    forcar = os.environ.get("FORCAR") == "1"
    if not (mudou_cmed or mudou_rol or forcar):
        log("\nNada mudou desde a ultima execucao. Encerrando sem republicar.")
        saida_workflow("mudou", "nao")
        return 0

    log(f"\nMudou -> CMED: {'sim' if mudou_cmed else 'nao'} | Rol: {'sim' if mudou_rol else 'nao'}")

    tmp = RAIZ / ".tmp"
    tmp.mkdir(exist_ok=True)
    log("baixando CMED...")
    baixa(url_cmed, tmp / "cmed.xlsx")
    log("baixando Anexo II...")
    baixa(url_rol, tmp / "anexo2.pdf")

    secoes = parse_anexo2(texto_do_pdf(tmp / "anexo2.pdf"))
    cmed, publicada = carrega_cmed(tmp / "cmed.xlsx")
    achados = cruza(secoes, cmed)
    log(f"diretrizes: {len(secoes)} | substancias CMED: {len(cmed)} | citadas no Rol: {len(achados)}")

    dec = le_curadoria()
    pendentes = sorted(s for s in achados if s not in dec)
    confirmados = {s: o for s, o in achados.items() if dec.get(s) == "CONFIRMA"}
    sumidos = sorted(s for s, d in dec.items() if d == "CONFIRMA" and s not in achados)

    if pendentes:
        log(f"\n!! {len(pendentes)} substancia(s) sem curadoria — NAO entram na ferramenta:")
        for s in pendentes:
            log(f"   - {s}")
    if sumidos:
        log(f"\n!! {len(sumidos)} substancia(s) curadas sumiram do Rol: {', '.join(sumidos)}")

    meta = {
        "rol": f"RN 465/2021, Anexo II, atualizado pela RN {rn}",
        "cmed": f"Lista de conformidade CMED/Anvisa — {publicada or arq_cmed}",
        "gerado": os.environ.get("DATA_HOJE", ""),
        "curadoria": f"Lista submetida a revisão técnica: {len(dec)} substâncias analisadas, "
                     f"{sum(1 for v in dec.values() if v == 'EXCLUI')} excluídas por citação "
                     f"incidental no texto das diretrizes.",
        "n_duts": len(secoes), "n_cmed": len(cmed),
    }
    dados = monta_dados(confirmados, cmed, secoes, meta)
    html = monta_html(dados)
    (RAIZ / "index.html").write_text(html, encoding="utf-8")

    n_ap = sum(m["na"] for m in dados["meds"])
    log(f"\nindex.html gerado: {len(dados['meds'])} medicamentos | {n_ap} apresentacoes")

    estado_arq.write_text(json.dumps({
        "cmed_arquivo": arq_cmed, "cmed_publicada": publicada,
        "rol_arquivo": arq_rol, "rol_rn": rn,
        "medicamentos": len(dados["meds"]), "apresentacoes": n_ap,
        "pendentes_de_curadoria": pendentes, "curadas_ausentes_do_rol": sumidos,
        "atualizado_em": os.environ.get("DATA_HOJE", ""),
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    saida_workflow("mudou", "sim")
    saida_workflow("rn", rn)
    saida_workflow("cmed", publicada or arq_cmed)
    saida_workflow("medicamentos", len(dados["meds"]))
    saida_workflow("pendentes", len(pendentes))
    saida_workflow("lista_pendentes", ", ".join(pendentes))
    saida_workflow("mudou_rol", "sim" if mudou_rol else "nao")
    saida_workflow("mudou_cmed", "sim" if mudou_cmed else "nao")
    saida_workflow("cmed_anterior", estado.get("cmed_publicada", "primeira execução"))
    saida_workflow("apresentacoes", n_ap)
    return 0

if __name__ == "__main__":
    sys.exit(main())
