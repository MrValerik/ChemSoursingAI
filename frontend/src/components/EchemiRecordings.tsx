import type { EchemiManualAudit, EchemiPointerRecording, EchemiSearch } from "../api/types";

const outcomes: Record<string, string> = { passed: "Проверка пройдена", manual_timeout: "Время проверки истекло",
  waiting_for_user: "Ожидание проверки", interrupted: "Проверка прервана" };
const stops: Record<string, string> = { passed: "Проверка пройдена", manual_timeout: "Время истекло",
  disconnected: "Окно закрыто", connection_error: "Ошибка соединения",
  challenge_disappeared: "Страница проверки закрылась", interrupted: "Запись прервана" };

function download(searchId: number, audit: EchemiManualAudit, recording: EchemiPointerRecording) {
  const payload = { search_id: searchId, verification_status: audit.status, stage: audit.stage,
    url: audit.url, recording };
  const url = URL.createObjectURL(new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" }));
  const link = document.createElement("a");
  link.href = url; link.download = `echemi-${searchId}-mouse-${recording.id}.json`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export default function EchemiRecordings({ search }: { search: EchemiSearch }) {
  const events = search.diagnostics.captcha;
  const audits = (Array.isArray(events) ? events : []).filter((event): event is EchemiManualAudit =>
    event?.mode === "manual" && Array.isArray(event.recordings));
  const count = audits.reduce((total, audit) => total + audit.recordings.length, 0);
  if (!count) return null;
  const running = search.status === "running";
  return <details className="echemi-recordings">
    <summary>Записи ручной проверки · {count}</summary>
    <p>Координаты и время событий в окне проверки. Данные сохраняются по мере поиска; скачивание во время записи содержит текущий снимок.</p>
    {audits.map((audit, index) => <div key={index}>
      <p><strong>{outcomes[!running && audit.status === "waiting_for_user" ? "interrupted" : audit.status] || audit.status}</strong>
        {audit.recordings_omitted ? ` · Не записано подключений из-за лимита: ${audit.recordings_omitted}` : ""}</p>
      {audit.recordings.map(recording => <div key={recording.id} className="echemi-recording-row">
        <span>{new Date(recording.started_at).toLocaleString("ru-RU")} · {recording.events.length} событий
          {` · ${recording.stop_reason ? stops[recording.stop_reason] || recording.stop_reason : running ? "Записывается" : "Прервана: сохранён последний снимок"}`}
          {recording.limit_reached && ` · Запись ограничена, пропущено ${recording.dropped_events} событий`}</span>
        <button onClick={() => download(search.id, audit, recording)}>Скачать JSON</button>
      </div>)}
    </div>)}
  </details>;
}
