import { useEffect, useState } from "react";

type Skill = { name: string; description: string; source: string; shared_with: string[] };
type Report = { enabled: boolean; excluded: string[]; receivers: string[]; skills: Skill[] };
const NAMES: Record<string, string> = { codex: "Codex", claude: "Claude Code", cursor: "Cursor" };

/** DAN settings: share skills installed for one agent CLI with the others, without touching their own folders. */
export function SkillPool() {
  const [data, setData] = useState<Report | null>(null);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    fetch("/api/skills").then(async (response) => { if (!response.ok) throw new Error("Skills could not be loaded."); setData(await response.json()); })
      .catch((caught) => setError(caught instanceof Error ? caught.message : String(caught)));
  }, []);
  async function save(enabled: boolean, excluded: string[]) {
    setSaving(true); setError("");
    try {
      const response = await fetch("/api/skills", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled, excluded }) });
      if (!response.ok) throw new Error("Skill sharing could not be saved.");
      setData(await response.json());
    } catch (caught) { setError(caught instanceof Error ? caught.message : String(caught)); }
    finally { setSaving(false); }
  }
  const receivers = data?.receivers.map((id) => NAMES[id] ?? id).join(", ") || "";
  const shareable = data?.skills.filter((skill) => !data.receivers.every((receiver) => receiver === skill.source)) ?? [];
  const sharedCount = data?.skills.filter((skill) => skill.shared_with.length).length ?? 0;
  return <section className="wb-skill-pool">
    <h3>Skills</h3>
    <p>Let {receivers || "agents"} use skills installed for your other agent CLIs. DAN links them at launch; your Codex, Claude, and Cursor folders are never changed.</p>
    {error && <p role="alert">{error}</p>}
    {data && <>
      <label className="wb-native-enabled"><input type="checkbox" checked={data.enabled} disabled={saving} onChange={(event) => void save(event.target.checked, data.excluded)} />Share skills across agents{data.enabled && sharedCount ? ` · ${sharedCount} shared` : ""}</label>
      {data.enabled && <ul className="wb-skill-list">{shareable.map((skill) => {
        const excluded = data.excluded.includes(skill.name);
        const blocked = !excluded && !skill.shared_with.length;  // same name already installed for the receiver
        return <li key={`${skill.source}:${skill.name}`}><label title={skill.description}>
          <input type="checkbox" checked={!excluded && !blocked} disabled={saving || blocked} onChange={(event) => void save(true, event.target.checked ? data.excluded.filter((name) => name !== skill.name) : [...data.excluded, skill.name])} />
          <span><strong>{skill.name}</strong><small>{NAMES[skill.source] ?? skill.source}{blocked ? " · already installed there" : ""}</small></span>
        </label></li>;
      })}</ul>}
    </>}
    {!data && !error && <p>Loading skills…</p>}
  </section>;
}
