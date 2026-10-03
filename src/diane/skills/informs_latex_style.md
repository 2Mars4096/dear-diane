---
type: style
name: INFORMS LaTeX Style
hook: pre_prompt
attach_to_tags: [latex]
---
Format the LaTeX manuscript using INFORMS conventions:
- Use document class: \documentclass[mnsc,nonblindrev]{informs3}
- Required packages: natbib (plainnat style), amsmath, graphicx, booktabs.
- Section ordering: Introduction, Literature Review, Model/Framework, Data & Methods (if empirical), Results, Discussion, Conclusion.
- Figures: use \begin{figure}[htbp] with descriptive captions below.
- Tables: use booktabs (\toprule, \midrule, \bottomrule) with captions above.
- Citations: use \citet{} for textual and \citep{} for parenthetical.
- Bibliography: \bibliographystyle{plainnat}, \bibliography{references}.
- Abstract: max 150 words. Keywords: 3-5 terms.
- Number all equations. Reference as Equation (1), not Eq. 1.
- Appendix for proofs and supplementary material.
