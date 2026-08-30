import sys, re, zlib
pdf = open(r"F:\1KeyTranscoder\_research\intel_avx512_x265.pdf","rb").read()
print("PDF bytes:", len(pdf))
# Try libraries
lib = None
for name in ("pypdf","PyPDF2","pdfminer"):
    try:
        __import__(name); lib = name; break
    except Exception:
        pass
print("lib:", lib)
if lib is None:
    # manual: find streams and inflate
    texts = []
    for m in re.finditer(rb'stream\r?\n(.*?)endstream', pdf, re.S):
        data = m.group(1)
        try:
            d = zlib.decompress(data)
        except Exception:
            continue
        # extract text between BT..ET roughly
        texts.append(d)
    blob = b"\n".join(texts)
    # crude text extraction
    out = []
    for mm in re.finditer(rb'\((?:[^()\\]|\\.)*\)\s*Tj', blob):
        out.append(mm.group(0))
    full = b"\n".join(out).decode("latin1", "ignore")
    open(r"F:\1KeyTranscoder\_research\intel_avx512_raw.txt","w",encoding="utf-8",errors="ignore").write(full)
    print("raw text chars:", len(full))
