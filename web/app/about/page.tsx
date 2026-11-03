export const metadata = { title: "About" };

export default function AboutPage() {
  return (
    <>
      <div className="page-head">
        <h1>About</h1>
      </div>
      <div className="card" style={{ maxWidth: 760 }}>
        <h2 style={{ marginTop: 0 }}>Mohamed El Askary</h2>
        <ul style={{ paddingLeft: 18 }}>
          <li>Forward Deployed Engineer at Oscilar and founder of Vantage Systems, an operational intelligence platform with production agents.</li>
          <li>Data science and analytics roles at GeoAct, Johnson and Johnson MedTech, Edwards Lifesciences, and the Egyptian Space Agency.</li>
          <li>U.S. Air Force, June 2021 to May 2024; commanded a squadron of 40; active Secret clearance.</li>
          <li>Competitive gymnast ranked 7th in the United States; national handstand record of 5 minutes 45 seconds, held since 2012.</li>
          <li>Chapman University: B.S. Data Science and B.S. Business Administration (Finance); MBA (Master of Business Administration) and MSBA (Master of Science in Business Analytics) expected 2027.</li>
        </ul>
        <div className="filters" style={{ marginTop: 12 }}>
          <a className="btn" href="#" aria-disabled="true">Resume (PDF, coming soon)</a>
          <a className="btn" href="#" aria-disabled="true">Repository (link coming soon)</a>
          <a className="btn" href="#" aria-disabled="true">Demo video (coming soon)</a>
        </div>
      </div>
      <div className="card" style={{ maxWidth: 760, marginTop: 14 }}>
        <h2 style={{ marginTop: 0 }}>How this works</h2>
        <p>Collectors pull public data (GitHub, arXiv, Hacker News, SEC EDGAR, USAspending) into a cache where every response keeps its fetch time and SHA-256 hash. Entity resolution joins mentions into companies, a scorer ranks them with an explainable breakdown, and agents write memos where every sentence cites a cached source and a separate verifier agent checks each claim.</p>
      </div>
    </>
  );
}
