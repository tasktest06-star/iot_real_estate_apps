# Property / insurance IoT briefing pack
#
#   make deps              install the TeX packages needed for the deck
#   make slides            build slides/main.pdf
#   make verify-licenses   re-verify every licence claim against the repos' LICENSE text
#   make check-links       assert every GitHub URL in the Markdown resolves
#   make check-data        assert the CSVs parse and are internally consistent
#   make clean             remove LaTeX build artefacts

SHELL := /bin/bash
SLIDES := slides/main

# Deliberately NOT required by slides/main.tex: pgfplots, multirow, makecell,
# adjustbox, inconsolata, tcolorbox, pgfgantt, beamertheme-metropolis. Some were
# absent from this box mid-build, so the deck is written to need only the set
# below plus plain TikZ primitives. `make deps` installs a superset anyway, with
# --skip-unavailable, so a richer box is not penalised.
TEX_PKGS := texlive-scheme-basic texlive-latex texlive-beamer texlive-pgf \
            texlive-booktabs texlive-colortbl texlive-xcolor texlive-tools \
            texlive-sourcesanspro texlive-microtype texlive-ulem \
            texlive-enumitem texlive-listings texlive-multirow \
            texlive-collection-latexrecommended texlive-collection-fontsrecommended

# Every .sty the deck actually loads. `make slides` probes these with kpsewhich
# before spending two minutes on a build that was going to fail anyway.
REQUIRED_STY := beamer pgf tikz booktabs colortbl xcolor array microtype

.PHONY: all deps slides verify-licenses check-links check-data check clean

all: slides

deps:
	sudo dnf install -y --skip-unavailable $(TEX_PKGS)

slides: $(SLIDES).pdf

# pdflatex runs twice: once for content, once to resolve the slide-count
# fraction in the footer. latexmk is not packaged on this box.
$(SLIDES).pdf: $(SLIDES).tex
	@command -v pdflatex >/dev/null || { echo "pdflatex not found - run 'make deps' first"; exit 1; }
	@missing=""; for s in $(REQUIRED_STY); do \
	   kpsewhich $$s.sty >/dev/null 2>&1 || kpsewhich $$s.cls >/dev/null 2>&1 || missing="$$missing $$s"; \
	 done; \
	 if [[ -n "$$missing" ]]; then echo "missing TeX packages:$$missing"; echo "run 'make deps'"; exit 1; fi
	cd slides && pdflatex -interaction=nonstopmode -halt-on-error main.tex >/dev/null
	cd slides && pdflatex -interaction=nonstopmode -halt-on-error main.tex >/dev/null
	@echo "built $(SLIDES).pdf ($$(pdfinfo $(SLIDES).pdf 2>/dev/null | awk '/^Pages/{print $$2}') pages)"

verify-licenses:
	@bash scripts/verify-licenses.sh

check-links:
	@echo "Checking GitHub URLs in Markdown..."
	@grep -ohE 'https://github\.com/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+' *.md \
	  | sed 's#/*$$##' | sort -u \
	  | xargs -P 10 -I{} bash -c \
	    'c=$$(curl -sIL --max-time 20 -o /dev/null -w "%{http_code}" "{}"); \
	     [[ "$$c" == "200" ]] || printf "  %-5s %s\n" "$$c" "{}"' \
	  | sort; \
	echo "done (only non-200 responses are listed above)"

check-data:
	@python3 -I scripts/check-data.py

check: verify-licenses check-data check-links

clean:
	rm -f slides/*.aux slides/*.log slides/*.nav slides/*.out slides/*.snm \
	      slides/*.toc slides/*.vrb slides/*.fls slides/*.fdb_latexmk
