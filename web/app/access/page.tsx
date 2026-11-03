export const metadata = { title: "Access" };

export default function AccessPage() {
  return (
    <div className="card" style={{ maxWidth: 420, margin: "40px auto" }}>
      <h1>Access code</h1>
      <p className="muted">This preview is private. Enter the access code you were given.</p>
      <form method="get" action="/">
        <label htmlFor="code" className="visually-hidden">Access code</label>
        <input id="code" name="code" type="search" autoComplete="off" style={{ width: "100%" }} />
        <button className="btn primary" style={{ width: "100%", marginTop: 10 }} type="submit">Continue</button>
      </form>
    </div>
  );
}
