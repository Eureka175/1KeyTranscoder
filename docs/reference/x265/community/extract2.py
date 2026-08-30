from pypdf import PdfReader
r = PdfReader(r"F:\1KeyTranscoder\_research\intel_avx512_x265.pdf")
full = "\n".join((p.extract_text() or "") for p in r.pages)
open(r"F:\1KeyTranscoder\_research\intel_avx512_text.txt","w",encoding="utf-8").write(full)
print("chars:", len(full), "pages:", len(r.pages))
