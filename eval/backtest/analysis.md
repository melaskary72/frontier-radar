## Analysis

### Test 1: Form D outcomes

The raw comparison looks dramatic and is misleading. The radar's top 100 went on to file new
Form Ds about 39 times as often as the average in-thesis entity over 6 months, and about 23
times as often over 12 months. But the radar's top 100 is mostly organizations and federal
awardees, while the in-thesis universe is 92% individual GitHub accounts, which almost never
file Form Ds. Compared like for like, each radar entry against the in-thesis rate of its own
entity type at the same cutoff, the lift is about 1.65 at 6 months and 1.13 at 12 months, and
both 95% intervals include 1. Every hit comes from two or three companies that recur across
cutoffs. The GitHub-stars baseline had no hits at all.

So this test does not show that the radar predicts raises. It shows something narrower and
real: the radar's top of list is made of actual companies rather than hobby accounts, which is
the precondition for being useful, not proof of foresight. The like-for-like control was added
after seeing the composition imbalance, and it is reported next to the raw figure rather than
in place of it.

### Test 2: Anti Fund's portfolio

Frontier Radar found none of the twelve companies before their rounds were announced. That is
not a ranking failure. None of the twelve appears anywhere in the collection: not in the
universe at either cutoff, not below the thesis gate, not outside the lookback window. The
radar never saw them, so it never had the chance to rank them.

The footprint check explains why. Before each company's T minus 30 cutoff, the public
technical record that thesis queries can reach was empty for every included primary company:

- Efference's first public repository was created six days after its round was reported.
- Trajectory's and Merge's first launch posts appeared on announcement day.
- Enigma's only earlier trace is a handful of low-point links to its blog, which are neither a
  launch post nor a repository.
- General Galactic's GitHub account has no public repositories.
- Westmag, Orbital, Aeon, and Catalyst have no public technical footprint that we could find.
- Pensive, in the secondary applied-AI set, did have public repositories nine months before
  its round. They are outside the frontier thesis queries by design, so missing them is the
  thesis doing its job, not a bug.

The pattern is consistent with how frontier companies are actually funded at seed and
pre-seed: the round comes before the public artifacts, not after them. Anti Fund is often the
first or one of the first checks, which is exactly the regime where public signal is thinnest.

### What this backtest can and cannot claim

- **Small n.** Twelve companies, nine with reliable dates. A zero here bounds what public data
  can do for this portfolio at seed; it says little about later stages.
- **Survivorship and selection.** The test set is Anti Fund's own picks. Companies the firm
  passed on, and companies that never raised, are not in it, so this measures whether the radar
  would have found the firm's winners, not whether its top 25 would have made good investments.
- **Point-in-time gaps.** Stars at the cutoff, hiring history, and current citations are not
  observable historically (BUILD_CONTRACT D1, D5). Those features are switched off in backtest
  mode, so the backtest scores a weaker radar than the live one.
- **Thesis filter.** The backtest filters with the classifier alone, about 68% accurate on
  Mohamed's labels, because an LLM judge reading historical text could carry hindsight about
  which companies later became famous (BUILD_CONTRACT D9). The live radar adds the judge for
  uncertain cases and measures about 90%.
- **Leakage.** The query audit passed: no discovery query names a target company or domain, and
  every company in every universe traces to a thesis query. The zero is not an artifact of
  hiding the answer and is not inflated by finding it.

### What would close the gap

Public code, papers, and launches arrive after the round for this kind of company, so a
sourcing system that only reads them will always be late at seed. The signals that come first
are private or relational:

- **Founder network intros:** who is talking to whom before a company has a name, from the
  firm's own portfolio founders and operators.
- **University lab relationships:** which PhD students and postdocs in robotics, energy, and
  defense labs are about to leave, known through advisors and lab alumni before any
  repository exists.
- **Conference and event attendee lists:** small technical workshops, defense and energy
  industry days, and the firm's own events, where founders appear months before a launch post.
- **Private hiring data:** early recruiting conversations and stealth job descriptions, which
  move before public job boards.

Frontier Radar is built to take these as additional evidence types: each would be a new
collector writing timestamped rows with a source, scored by the same point-in-time features
and cited the same way in memos. The public layer still earns its place after the first check:
it tracks portfolio momentum, catches the later-stage company that ships in public, and makes
every claim in a memo traceable. But the honest result of this backtest is that, for Anti
Fund's seed-stage frontier picks, the edge is in relationships, and the software should be
built to capture and structure them.
