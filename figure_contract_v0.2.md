# Final census figure contract v0.2

Core conclusion: the release candidate makes publication-to-endpoint static
change reusable by preserving the selection path, paired immutable archives,
project-level deltas and family-specific completeness in one auditable object.

- Figure archetype: schematic-led quantitative composite.
- Backend: Python/matplotlib only.
- Target/output: Scientific Data Data Overview; double-column width (183 mm),
  approximately 116 mm high; editable text in SVG/PDF plus 600-dpi PNG/TIFF.
- Panel a: project attrition from the frozen 940-record JOSS frame through
  metadata, publication-archive, endpoint and paired extraction stages. This
  is scope and availability evidence, not a quality or prevalence estimate.
- Panel b: project-level paired median changes for six static metrics. Points
  are endpoint minus publication medians; bars are deterministic, nonparametric
  project-resampling percentile intervals. These are descriptive summaries and
  are not significance tests.
- Panel c: family-specific project proportions for complete paired comparisons
  and for at least one added, removed or changed feature. Denominators are
  project-level and family-specific; partial/unavailable states remain in the
  source tables.
- Source data: `figures/census_v0.2/census_overview_source_data.csv`, generated
  from `data/census_v0.2/census_summary.json` and `summary_tables/`.
- Integrity: no candidate software is installed, imported or executed; all
  plotted values are regenerated from validated tables. The caption must state
  that extracted-row volumes and static changes are not software quality,
  maintenance, runtime compatibility or impact scores.
- Review risks: the 27 unresolved endpoints and parser-error records must stay
  visible in the accompanying tables and text; no denominator may silently
  switch from the 372 archive-eligible projects to the 345 resolved pairs.
