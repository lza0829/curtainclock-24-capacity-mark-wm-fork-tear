import { useEffect, useState } from "react";
import { api } from "../api";

export default function Home() {
  const [d, setD] = useState(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    api
      .dashboard()
      .then(setD)
      .catch((e) => setErr(e.message));
  }, []);

  if (err) return <div className="msg err">{err}</div>;
  if (!d) return <div className="panel">加载中…</div>;

  return (
    <section className="panel">
      <h2>今日水位 · {d.fixture_day}</h2>
      <div className="stats">
        <div className="stat">
          在途租约
          <b>{d.open_leases}</b>
        </div>
        <div className="stat">
          冲突计数
          <b>{d.conflict_count}</b>
        </div>
        <div className="stat">
          超额拒绝
          <b>{d.overage_conflicts}</b>
        </div>
        <div className="stat">
          占用中
          <b>{d.live_sessions}</b>
        </div>
        <div className="stat">
          待开窗
          <b>{d.draft_sessions}</b>
        </div>
        <div className="stat">
          超额窗（钉扎）
          <b>{d.overage_windows}</b>
        </div>
        <div className="stat">
          超额在途
          <b>{d.overage_open}</b>
        </div>
      </div>
      <p className="hint">
        超额口径：{d.overage_mode === "reject" ? "整笔拒绝" : d.overage_mode}；
        预估并行度 &gt; 额定并发时开窗整笔失败、写超额冲突、零 open、水位不加，
        故超额窗恒为 0。
      </p>
    </section>
  );
}
