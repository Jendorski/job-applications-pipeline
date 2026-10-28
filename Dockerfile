# syntax=docker/dockerfile:1.7
FROM python:3.14.10-slim-trixie

# LibreOffice for docx → PDF, poppler-utils for page previews.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libreoffice-writer-nogui fontconfig poppler-utils tini \
 && rm -rf /var/lib/apt/lists/*

# EB Garamond (OFL) stands in for Microsoft Garamond in PDFs. Debian's fonts-ebgaramond ships an
# incomplete Bold, so use Octavio Pardo's complete static cuts, pinned by commit and checksum.
ARG EBG=https://raw.githubusercontent.com/octaviopardo/EBGaramond12/106a4a6d377987459ae5e68673a4570f13b957fb/fonts/ttf
ADD --checksum=sha256:2028dc06d3c130b4761693481436a32a8e35ed500bf58c25c53de004106125b8 ${EBG}/EBGaramond-Regular.ttf /usr/share/fonts/truetype/ebgaramond/
ADD --checksum=sha256:d4ad1d0a9390d26d6d3f176117a1d121441edb8c8632bfca52cfad084b9059cb ${EBG}/EBGaramond-Italic.ttf /usr/share/fonts/truetype/ebgaramond/
ADD --checksum=sha256:0cfed122e51e3fd44ccedaef7637efed6d5bdc4ad89a6117d70241510309a186 ${EBG}/EBGaramond-Bold.ttf /usr/share/fonts/truetype/ebgaramond/
ADD --checksum=sha256:3437cf76a720b01580218614e7f0ce9d66035a075c34a98394cec9a888cf38d6 ${EBG}/EBGaramond-BoldItalic.ttf /usr/share/fonts/truetype/ebgaramond/
COPY docker/fontconfig/30-garamond.conf /etc/fonts/conf.d/30-garamond.conf
RUN chmod 644 /usr/share/fonts/truetype/ebgaramond/*.ttf && fc-cache -f

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
# The example profile, so `jap init` can create profile/ from inside the container.
COPY examples ./examples
# Editable, so compose can mount ./src over it during development without a rebuild.
# pip is then removed: it vendors msgpack, urllib3 and pkg_resources, which carry the image's
# only fixable high CVEs, and nothing needs it at runtime.
RUN pip install --no-cache-dir -e . \
 && pip uninstall -y pip \
 && rm -rf /usr/local/lib/python3.14/ensurepip

RUN useradd -m -u 1000 jap
USER jap
ENV JAP_HOME=/app
ENTRYPOINT ["tini", "--"]
CMD ["jap", "watch"]
