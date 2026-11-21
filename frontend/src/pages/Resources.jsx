import { useEffect, useState } from "react";
import { api } from "../api";

export default function Resources() {
  const [slots, setRooms] = useState([]);
  const [executors, setTeachers] = useState([]);
  const [drafts, setDrafts] = useState({});
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");

  async function load() {
    const [r, t] = await Promise.all([api.slots(), api.executors()]);
    setRooms(r);
    setTeachers(t);
  }

  useEffect(() => {
    load().catch((e) => setErr(e.message));
  }, []);

  async function saveCapacity(code) {
    setMsg("");
    setErr("");
    const raw = drafts[code];
    try {
      const r = await api.setCapacity(code, Number(raw));
      setMsg(
        `已改额定并发 ${r.code} → ${r.rated_concurrency}；未开窗批窗将按新额定重新裁决`
      );
      await load();
    } catch (e) {
      setErr(e.message);
      await load();
    }
  }

  if (err && !slots.length) return <div className="msg err">{err}</div>;

  return (
    <>
      <section className="panel">
        <h2>算力槽</h2>
        {msg && <div className="msg">{msg}</div>}
        {err && <div className="msg err">{err}</div>}
        <p className="hint">
          额定并发即开窗裁决与租约快照的唯一来源。改额定与开窗互斥：仍有占用中租约时须先关窗。
        </p>
        <table>
          <thead>
            <tr>
              <th>编码</th>
              <th>名称</th>
              <th>额定并发</th>
              <th>在途租约</th>
              <th>超额在途</th>
              <th>状态</th>
              <th>改额定</th>
            </tr>
          </thead>
          <tbody>
            {slots.map((r) => (
              <tr key={r.code}>
                <td>{r.code}</td>
                <td>{r.name}</td>
                <td>{r.rated_concurrency}</td>
                <td>{r.open_leases}</td>
                <td>{r.overage_open}</td>
                <td>{r.active ? "启用" : "停用"}</td>
                <td>
                  <div className="row-actions">
                    <input
                      type="number"
                      min="1"
                      className="narrow"
                      placeholder={String(r.rated_concurrency)}
                      value={drafts[r.code] ?? ""}
                      onChange={(e) =>
                        setDrafts((d) => ({ ...d, [r.code]: e.target.value }))
                      }
                    />
                    <button
                      className="secondary"
                      disabled={!drafts[r.code] || r.open_leases > 0}
                      title={
                        r.open_leases > 0
                          ? "仍有占用中租约，须先关窗"
                          : "改额定并发"
                      }
                      onClick={() => saveCapacity(r.code)}
                    >
                      保存
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <section className="panel">
        <h2>执行器</h2>
        <table>
          <thead>
            <tr>
              <th>编码</th>
              <th>名称</th>
              <th>作业类型</th>
              <th>状态</th>
            </tr>
          </thead>
          <tbody>
            {executors.map((t) => (
              <tr key={t.code}>
                <td>{t.code}</td>
                <td>{t.name}</td>
                <td>{t.subject}</td>
                <td>{t.active ? "启用" : "停用"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  );
}
