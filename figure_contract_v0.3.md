# Scientific Data figure contract v0.3

This contract supersedes the single-composite v0.2 overview figure for the
current manuscript. The four figures are descriptive data-object and validation
views; none is an inferential effect plot.

## Shared rules

- Backend: Python/matplotlib; values are regenerated from validated JSON/CSV
  outputs by `scripts/make_scidata_figures.py`.
- Manuscript assets: vector PDF/SVG plus PNG/TIFF exports, with one source-data
  CSV per figure under `figures/census_v0.2/`.
- Static boundary: no candidate software is installed, imported or executed.
- Every caption states the relevant denominator and that static observations are
  not runtime compatibility, quality, maintenance or prevalence estimates.
- The figure source-data files are release inputs, not hand-entered chart data.

## Figure specifications

### Figure 1: Pairing workflow and data object

Panel a shows the deterministic 940-record JOSS frame through 396
metadata-eligible, 372 archive-eligible and 345 endpoint-resolved/paired
projects, retaining unresolved states. Panel b shows one immutable publication
archive paired with one immutable endpoint per resolved project. Panel c lists the
seven core table families, evidence links and the no-execution boundary.

Source data: `fig1_pairing_workflow_source_data.csv`.

### Figure 2: Project-level change landscape

Panel a is a project-by-family heatmap of added, removed and changed fractions
among comparable rows, with partial/unavailable cells retained as white. Panel b
summarizes complete project-family comparisons. The plot exposes within-project
churn without converting it into a quality or compatibility score.

Source data: `fig2_change_landscape_source_data.csv`.

### Figure 3: Metadata and CI transitions

Four slope panels show publication-to-endpoint presence proportions for
`pyproject.toml`, GitHub Actions workflows, Travis CI and `CITATION.cff`. Labels
give added and removed project counts. Presence is a binary file/configuration
observation and does not imply content quality or workflow success.

Source data: `fig3_metadata_transitions_source_data.csv`.

### Figure 4: Completeness and validation evidence

Panel a shows API/dependency/structure parsing completeness for publication,
endpoint and paired scopes. Panel b shows exact agreement in the independent
24-project semantic audit. Panel c shows retained parser events. Panel d shows
the independent reconstruction and archive/evidence checks. These panels document
data-generation boundaries and audit scope, not population parser accuracy.

Source data: `fig4_validation_audit_source_data.csv`.

## Acceptance checks

The current PDF contains all four figures with self-contained captions. The
public v0.2.3 package contains this contract, the four source-data CSVs and the
figure-generation script. A final submission build must repeat the PDF render
check after any manuscript-layout change; Scientific Data does not require or
recommend a journal-specific LaTeX template.
