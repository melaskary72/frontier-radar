# Labels

Hand-labeled sets used by the eval gates. Every file records who labeled each row.

## thesis_labels.csv (thesis classifier gate: at least 85% accuracy)

117 real items sampled at random (seed 11) from a live collection on 2026-11-01: 70 GitHub
repositories, 25 Hacker News launches, 22 federal awards (51 labeled 1, 66 labeled 0).
Three sampled awards were excluded before labeling: one portfolio company (names stay out of
training data) and two near-duplicate dental-supply rows.

Rubric v1. Label 1 when the text describes substantive technology in a thesis sector
(robotics and autonomy, defense, energy, space, manufacturing and industrial AI,
semiconductors and compute) that a company or research group is building. Label 0 for:

- tutorials, courses, workshops, hackathons, student or "learning" projects, simulation studies
- hobby or consumer tools that only touch a sector (satellite pass trackers, FPV tuners)
- software applications outside the sectors, even when they use sector words (FPGA for trading,
  crop-yield apps on radar imagery)
- directories, maps, lists, downloads, reverse-engineering notes
- federal awards for commodities, services, resale, or keyword collisions (dental "fusion" wedges)

Labeled by Claude Code under this rubric; Mohamed El Askary spot-checks a sample at the
Phase 4 review. Disagreements are recorded in docs/09-change-log.md.
