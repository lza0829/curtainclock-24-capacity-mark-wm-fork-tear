import { useEffect, useState } from "react";
import { api } from "../api";

function fmtMin(m) {
  const h = String(Math.floor(m / 60)).padStart(2, "0");
  const mm = String(m % 60).padStart(2, "0");
  return `${h}:${mm}`;
}

const statusLabel = { draft: "待开窗", live: "占用中", done: "已结束" };

export default function Sessions() {
  const [rows, setRows] = useState([]);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [form, setForm] = useState({
    code: "",
    title: "",
    subject: "ETL",
    slot_code: "S-ETL-01",
    executor_code: "E-PIPE-01",
    day: "2026-11-20",
    start: "09:00",
    end: "10:00",
    parallelism: 10,
  });

  async function load() {
    setErr("");
    try {
      setRows(await api.windows());
    } catch (e) {
      setErr(e.message);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function openLease(code) {
    setMsg("");
    setErr("");
    try {
      const r = await api.openLease(code);
      setMsg(
        `已开窗，租约 ${r.lease_no}；并行度 ${r.parallelism}/额定 ${r.rated_concurrency}；在途租约 ${r.open_leases}`
      );
      await load();
    } catch (e) {
      setErr(e.message);
      await load();
    }
  }

  function update(k, v) {
    setForm((f) => ({ ...f, [k]: v }));
  }

  async function create(e) {
    e.preventDefault();
    setMsg("");
    setErr("");
    const [sh, sm] = form.start.split(":").map(Number);
    const [eh, em] = form.end.split(":").map(Number);
    try {
      const r = await api.createWindow({
        code: form.code,
        title: form.title,
        subject: form.subject,
        slot_code: form.slot_code,
        executor_code: form.executor_code,
        day: form.day,
        start_min: sh * 60 + sm,
        end_min: eh * 60 + em,
        parallelism: Number(form.parallelism),
      });
      setMsg(
        `已建批窗 ${r.code}：并行度 ${r.admission.parallelism}/额定 ${r.admission.rated_concurrency}`
      );
      await load();
    } catch (e2) {
      setErr(e2.message);
    }
  }

  return (
    <>
      <section className="panel">
        <h2>新建批窗</h2>
        <form className="form-grid" onSubmit={create}>
          <label>
            批窗编码
            <input
              value={form.code}
              onChange={(e) => update("code", e.target.value)}
              placeholder="W-XXX-01"
              required
            />
          </label>
          <label>
            标题
            <input
              value={form.title}
              onChange={(e) => update("title", e.target.value)}
              required
            />
          </label>
          <label>
            算力槽
            <input
              value={form.slot_code}
              onChange={(e) => update("slot_code", e.target.value)}
              required
            />
          </label>
          <label>
            执行器
            <input
              value={form.executor_code}
              onChange={(e) => update("executor_code", e.target.value)}
              required
            />
          </label>
          <label>
            预估并行度
            <input
              type="number"
              min="1"
              value={form.parallelism}
              onChange={(e) => update("parallelism", e.target.value)}
              required
            />
          </label>
          <label>
            日期
            <input
              type="date"
              value={form.day}
              onChange={(e) => update("day", e.target.value)}
              required
            />
          </label>
          <label>
            开始
            <input
              type="time"
              value={form.start}
              onChange={(e) => update("start", e.target.value)}
              required
            />
          </label>
          <label>
            结束
            <input
              type="time"
              value={form.end}
              onChange={(e) => update("end", e.target.value)}
              required
            />
          </label>
          <div className="form-actions">
            <button type="submit">建立批窗</button>
          </div>
        </form>
      </section>

      <section className="panel">
        <h2>批窗</h2>
        {msg && <div className="msg">{msg}</div>}
        {err && <div className="msg err">{err}</div>}
        <table>
          <thead>
            <tr>
              <th>批窗</th>
              <th>时段</th>
              <th>算力槽 / 执行器</th>
              <th>并行度 / 额定</th>
              <th>就绪</th>
              <th>状态</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.code}>
                <td>
                  <div>{r.title}</div>
                  <small>{r.code}</small>
                </td>
                <td>
                  {r.day}
                  <br />
                  {fmtMin(r.start_min)}–{fmtMin(r.end_min)}
                </td>
                <td>
                  {r.slot_code}
                  <br />
                  {r.executor_code}
                </td>
                <td>
                  {r.admission.parallelism} / {r.admission.rated_concurrency}
                  {r.admission.overage && (
                    <span className="tag bad">超额</span>
                  )}
                </td>
                <td>
                  {r.readiness?.ready ? (
                    <span className="tag live">就绪</span>
                  ) : (
                    <span className="tag bad">
                      {(r.readiness?.reasons || []).join("；") || "未就绪"}
                    </span>
                  )}
                </td>
                <td>
                  <span className={`tag ${r.status}`}>
                    {statusLabel[r.status] || r.status}
                  </span>
                </td>
                <td>
                  <button
                    disabled={r.status !== "draft" || r.admission.overage}
                    onClick={() => openLease(r.code)}
                  >
                    开窗占用
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  );
}
