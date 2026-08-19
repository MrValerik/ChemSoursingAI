// Раздел «Обратная связь»: что мешает и что непонятно.
//
// Это не служба поддержки — ответов и сроков программа не обещает. Задача
// проще и важнее: узнать, чего в ней не хватает, словами самого закупщика,
// а не пересказом через третьи руки.

import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { FeedbackMessage } from "../api/types";
import { useAuth } from "../auth/AuthContext";

// Раздел, из которого пришли. «Не хватает колонки» без этого приходится
// угадывать, а спросить автора удаётся не всегда.
const ORIGIN_LABELS: Record<string, string> = {
  requests: "Запросы",
  substances: "Химические вещества",
  suppliers: "Поставщики",
  intermediaries: "Посредники",
  review: "Ручной разбор",
  templates: "Шаблоны",
  prompts: "ИИ-промпты",
  "communication-testing": "Тестирование общения",
  settings: "Настройки",
};

const ORIGIN_OPTIONS = ["", ...Object.keys(ORIGIN_LABELS)];

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString("ru-RU", { dateStyle: "long", timeStyle: "short" });
}

export default function FeedbackSection() {
  const { user } = useAuth();
  // Аудитору здесь писать можно, хотя во всей остальной программе он
  // только читает: это не правка данных, а сообщение о нехватке, а
  // читает программу он внимательнее прочих.
  const seesEveryone = user?.role === "admin" || user?.role === "head";

  const [text, setText] = useState("");
  const [origin, setOrigin] = useState("");
  const [messages, setMessages] = useState<FeedbackMessage[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState(false);

  const load = async () => {
    try {
      setMessages(await api.listFeedback());
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const send = async () => {
    if (!text.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      await api.sendFeedback({ text: text.trim(), origin: origin || null });
      setText("");
      setOrigin("");
      setSent(true);
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const mine = useMemo(
    () => messages.filter((item) => item.author_id === user?.id),
    [messages, user?.id],
  );
  // Руководитель и администратор видят всё, остальные — только своё.
  const shown = seesEveryone ? messages : mine;

  return (
    <div className="panel feedback-section">
      <h1>Обратная связь</h1>
      <p className="note">
        Напишите, чего вам не хватает или что непонятно: «не хватает колонки во
        вкладке Запросы», «непонятно, откуда берётся балл поставщика». Пишите
        своими словами и без подробностей об устройстве программы — мы
        разберёмся. Это не служба поддержки: ответ придёт не сразу и не на
        каждое сообщение, но прочитано будет каждое.
      </p>

      <div className="feedback-form">
        <label>
          О каком разделе речь
          <select
            value={origin}
            onChange={(event) => {
              setOrigin(event.target.value);
              setSent(false);
            }}
          >
            {ORIGIN_OPTIONS.map((key) => (
              <option key={key || "none"} value={key}>
                {key ? ORIGIN_LABELS[key] : "не важно / вся программа"}
              </option>
            ))}
          </select>
        </label>
        <label>
          Сообщение
          <textarea
            rows={6}
            value={text}
            maxLength={4000}
            placeholder="Например: не хватает колонки со сроком поставки во вкладке Запросы"
            onChange={(event) => {
              setText(event.target.value);
              setSent(false);
            }}
          />
        </label>
        <div className="feedback-actions">
          <button
            type="button"
            onClick={() => void send()}
            disabled={busy || !text.trim()}
            title={!text.trim() ? "Сначала напишите сообщение" : undefined}
          >
            Отправить
          </button>
          <span className="note">{text.length} из 4000</span>
        </div>
        {sent && (
          <p className="success-note">
            Отправлено. Сообщение видно ниже — значит, оно дошло.
          </p>
        )}
      </div>

      {error && <p className="error">{error}</p>}

      <h2>{seesEveryone ? "Все сообщения" : "Отправленные вами"}</h2>
      {shown.length === 0 ? (
        <p className="note">
          {seesEveryone
            ? "Пока никто ничего не написал."
            : "Вы ещё ничего не отправляли."}
        </p>
      ) : (
        <ul className="feedback-list">
          {shown.map((item) => (
            <li key={item.id}>
              <div className="feedback-meta">
                <span>{formatDate(item.created_at)}</span>
                {seesEveryone && item.author_name && (
                  <span>· {item.author_name}</span>
                )}
                {item.origin && (
                  <span>· {ORIGIN_LABELS[item.origin] ?? item.origin}</span>
                )}
              </div>
              <p>{item.text}</p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
