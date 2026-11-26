"use client";

// 一個模組一個聊天室（#108，老師 2026-11-26 決定）
// - 整個模組是一間連續的聊天室；原本的關卡是聊天室裡的「階段」，上方顯示每個階段的進度
// - 訊息送到目前階段；@ 評分機器人或按「送出評分」只評這個階段的對話
// - 階段通過後自動進到下一個階段（onStageChange 通知頁面切換，評分與回饋、前面傳來的資料跟著切換）
// 後端：GET /api/chat/modules/{module_id}?step_id=（backend/api/chat.py）

import { useEffect, useState } from "react";
import { ApiError, apiFetch } from "@/utils/api";
import { A } from "../../theme";
import ChatRoom, { type ChatMessage, type ChatRoomInfo, type ChatStage } from "./ChatRoom";

type Stage = ChatStage & {
  is_required: boolean;
  passed: boolean;
  locked: boolean;
  locked_by: string | null;
  form_pending?: boolean;
};

type ModuleRoom = {
  module_id: string;
  module_title: string;
  stages: Stage[];
  current_step_id: string | null;
  completed: boolean;
  messages: ChatMessage[];
  room: ChatRoomInfo | null;
};

export default function ModuleChatRoom({
  moduleId,
  stepId,
  sessionId,
  disabledReason,
  refreshKey,
  onEvaluationStart,
  onEvaluationEvent,
  onFinished,
  onStageChange,
  onUnavailable,
}: {
  moduleId: string;
  stepId: string;
  sessionId: string;
  disabledReason?: string;
  refreshKey: number;
  onEvaluationStart: () => void;
  onEvaluationEvent: (event: string, data: Record<string, unknown>) => void;
  onFinished: () => void;
  // 換階段：manual = 學生點上方的階段；否則是後端判定的目前階段和頁面選的不一樣（例如剛通過、自動進到下一階段）
  onStageChange: (stepId: string, manual: boolean) => void;
  // 讀不到模組聊天室（例如還沒更新資料庫）：頁面改用原本的畫面
  onUnavailable: () => void;
}) {
  const [data, setData] = useState<ModuleRoom | null>(null);
  const [error, setError] = useState("");
  // 剛在這個階段送出評分：重新讀取時不指定階段，讓後端決定是不是進到下一階段
  const [advanceFrom, setAdvanceFrom] = useState<{ stepId: string; n: number } | null>(null);

  useEffect(() => {
    let active = true;
    const query = advanceFrom?.stepId === stepId ? "" : `?step_id=${encodeURIComponent(stepId)}`;
    apiFetch<{ data: ModuleRoom }>(`/api/chat/modules/${encodeURIComponent(moduleId)}${query}`).then(
      (res) => {
        if (!active) return;
        setData(res.data);
        setError("");
        setAdvanceFrom(null);
        if (res.data.current_step_id && res.data.current_step_id !== stepId) onStageChange(res.data.current_step_id, false);
      },
      (err) => {
        if (!active) return;
        if (err instanceof ApiError && (err.status === 503 || err.status === 404)) onUnavailable();
        else setError(err instanceof ApiError ? err.message : "讀不到聊天室，請稍後再試");
      }
    );
    return () => {
      active = false;
    };
    // onStageChange / onUnavailable 每次 render 都是新的函式，不放進相依（只在模組、階段、重新整理時讀）
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [moduleId, stepId, refreshKey, advanceFrom]);

  if (error) return <div style={box}><p style={{ margin: 0, fontSize: "13px", color: "#a3361f" }}>{error}</p></div>;
  if (!data) return <div style={box}><p style={{ margin: 0, fontSize: "13px", color: "#6b6255" }}>聊天室載入中…</p></div>;
  if (!data.room) return <div style={box}><p style={{ margin: 0, fontSize: "13px", color: "#6b6255" }}>這個模組還沒有任何階段。</p></div>;

  const passedRequired = data.stages.filter((s) => s.is_required && s.passed).length;
  const required = data.stages.filter((s) => s.is_required).length;

  const header = (
    <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
      <div style={{ display: "flex", gap: "6px", flexWrap: "wrap" }}>
        {data.stages.map((s) => {
          const current = s.step_id === data.room!.step_id;
          return (
            <button
              key={s.step_id}
              type="button"
              disabled={s.locked || current}
              onClick={() => onStageChange(s.step_id, true)}
              title={s.locked ? `請先通過「${s.locked_by}」` : s.passed ? "已通過，可以回去看或再練習" : undefined}
              style={stageChip(current, s.passed, s.locked)}
            >
              {s.passed ? "✓ " : s.locked ? "🔒 " : ""}
              {s.step_order}. {s.step_title}
              {!s.is_required && <span style={{ opacity: 0.7 }}>（選修）</span>}
            </button>
          );
        })}
      </div>
      {required > 0 && (
        <div style={{ height: "8px", borderRadius: "9999px", backgroundColor: A.line, overflow: "hidden" }}>
          <div style={{ width: `${(passedRequired / required) * 100}%`, height: "100%", borderRadius: "9999px", backgroundColor: A.brand }} />
        </div>
      )}
      <div style={{ fontSize: "12px", color: data.completed ? A.primary : A.muted }}>
        {data.completed
          ? "✓ 這個模組的必修階段都通過了"
          : `必修階段 ${passedRequired} / ${required} 通過`}
        {data.stages.find((s) => s.step_id === data.room!.step_id)?.form_pending && "・這個階段還有表單要填，填完才算通過"}
      </div>
    </div>
  );

  return (
    <ChatRoom
      key={data.room.step_id}
      room={data.room}
      messages={data.messages}
      stages={data.stages}
      header={header}
      sessionId={sessionId}
      disabledReason={disabledReason}
      onEvaluationStart={onEvaluationStart}
      onEvaluationEvent={onEvaluationEvent}
      onFinished={(evaluated) => {
        onFinished();
        if (evaluated) setAdvanceFrom((prev) => ({ stepId: data.room!.step_id, n: (prev?.n ?? 0) + 1 }));
      }}
    />
  );
}

const box: React.CSSProperties = {
  backgroundColor: "#fbf8f1",
  borderRadius: "6px",
  padding: "16px",
  border: "1px solid #e4dccb",
};

function stageChip(current: boolean, passed: boolean, locked: boolean): React.CSSProperties {
  // 方案 A 的階段條：目前＝主色淡底＋主色字；已通過＝主色字；鎖住＝停用色
  return {
    padding: "4px 12px",
    fontSize: "12px",
    borderRadius: "6px",
    border: `1px solid ${current ? A.primary : A.line}`,
    backgroundColor: current ? A.primarySoft : A.page,
    color: locked ? A.faint : passed || current ? A.primary : A.muted,
    fontWeight: current ? 700 : 400,
    cursor: locked || current ? "default" : "pointer",
  };
}
