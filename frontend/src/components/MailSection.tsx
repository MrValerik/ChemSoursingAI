import { useCallback, useEffect, useMemo, useState } from "react";
import { api, userErrorMessage } from "../api/client";
import type {
  EmailSyncRead,
  MailboxFolder,
  MailboxMessageRead,
} from "../api/types";
import { useAuth } from "../auth/AuthContext";

const FOLDERS: Array<{ key: MailboxFolder; label: string }> = [
  { key: "all", label: "Все письма" },
  { key: "inbox", label: "Входящие" },
  { key: "sent", label: "Отправленные" },
  { key: "unresolved", label: "Неопределённые" },
];

const shortText = (value: string | null, max = 120) => {
  const text = (value || "").replace(/\s+/g, " ").trim();
  return text.length > max ? `${text.slice(0, max)}…` : text;
};

const formatDate = (value: string) =>
  new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));

function syncNotice(result: EmailSyncRead) {
  if (result.errors.length) {
    return `Проверка завершена с ошибками: ${result.errors.join("; ")}`;
  }
  return (
    `Почта обновлена: получено ${result.fetched}, сохранено ${result.processed}, ` +
    `неопределённых ${result.unmatched}.`
  );
}

export default function MailSection() {
  const { user } = useAuth();
  const canWrite = user?.role !== "auditor";
  const [folder, setFolder] = useState<MailboxFolder>("all");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [search, setSearch] = useState("");
  const [items, setItems] = useState<MailboxMessageRead[]>([]);
  const [total, setTotal] = useState(0);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [sending, setSending] = useState(false);
  const [downloadBusy, setDownloadBusy] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [composing, setComposing] = useState(false);
  const [replyToId, setReplyToId] = useState<number | null>(null);
  const [toAddress, setToAddress] = useState("");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const result = await api.listMailboxMessages({
        folder,
        date_from: dateFrom || undefined,
        date_to: dateTo || undefined,
        query: search || undefined,
      });
      setItems(result.items);
      setTotal(result.total);
      setSelectedId((current) =>
        result.items.some((item) => item.id === current)
          ? current
          : result.items[0]?.id ?? null,
      );
      setError(null);
    } catch (caught) {
      setError(userErrorMessage(caught));
    } finally {
      setLoading(false);
    }
  }, [dateFrom, dateTo, folder, search]);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 250);
    return () => window.clearTimeout(timer);
  }, [load]);

  const selected = useMemo(
    () => items.find((item) => item.id === selectedId) ?? null,
    [items, selectedId],
  );

  const startCompose = (message?: MailboxMessageRead) => {
    if (message) {
      setReplyToId(message.id);
      setToAddress(
        message.direction === "inbound"
          ? message.from_address || ""
          : message.to_address || "",
      );
      const source = message.subject || "";
      setSubject(/^re:/i.test(source) ? source : `Re: ${source}`);
    } else {
      setReplyToId(null);
      setToAddress("");
      setSubject("");
    }
    setBody("");
    setComposing(true);
    setNotice(null);
  };

  const sync = async () => {
    setSyncing(true);
    setNotice(null);
    try {
      const result = await api.syncEmailCommunications(100);
      setNotice(syncNotice(result));
      await load();
    } catch (caught) {
      setError(userErrorMessage(caught));
    } finally {
      setSyncing(false);
    }
  };

  const send = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!toAddress.trim() || !subject.trim() || !body.trim()) return;
    if (!window.confirm(`Отправить письмо на ${toAddress.trim()}?`)) return;
    setSending(true);
    try {
      const message = await api.sendMailboxMessage({
        to_address: toAddress.trim(),
        subject: subject.trim(),
        body: body.trim(),
        idempotency_key: crypto.randomUUID(),
        reply_to_message_id: replyToId,
        confirm_external_send: true,
      });
      setComposing(false);
      setNotice("Письмо отправлено и сохранено в общей почте.");
      setFolder("all");
      await load();
      setSelectedId(message.id);
    } catch (caught) {
      setError(userErrorMessage(caught));
    } finally {
      setSending(false);
    }
  };

  const downloadAttachment = async (
    documentId: number,
    filename: string,
  ) => {
    setDownloadBusy(documentId);
    setError(null);
    try {
      const blob = await api.downloadDocument(documentId);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (caught) {
      setError(userErrorMessage(caught));
    } finally {
      setDownloadBusy(null);
    }
  };

  return (
    <section className="mail-page">
      <header className="mail-header">
        <div>
          <h1>Почта</h1>
          <p>Общий служебный ящик: RFQ-переписка и несвязанные входящие.</p>
        </div>
        <div className="mail-header-actions">
          {canWrite && (
            <button className="secondary" onClick={() => void sync()} disabled={syncing}>
              {syncing ? "Проверка…" : "Проверить входящие"}
            </button>
          )}
          {canWrite && (
            <button onClick={() => startCompose()}>Написать письмо</button>
          )}
        </div>
      </header>

      <div className="mail-filters" aria-label="Фильтры почты">
        <nav className="mail-folders" aria-label="Папки">
          {FOLDERS.map((item) => (
            <button
              key={item.key}
              className={folder === item.key ? "is-active" : ""}
              onClick={() => setFolder(item.key)}
            >
              {item.label}
            </button>
          ))}
        </nav>
        <label>
          Поиск
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Адрес, тема или текст"
          />
        </label>
        <label>
          С даты
          <input type="date" value={dateFrom} onChange={(event) => setDateFrom(event.target.value)} />
        </label>
        <label>
          По дату
          <input type="date" value={dateTo} onChange={(event) => setDateTo(event.target.value)} />
        </label>
        {(dateFrom || dateTo || search) && (
          <button
            className="secondary mail-reset"
            onClick={() => {
              setDateFrom("");
              setDateTo("");
              setSearch("");
            }}
          >
            Сбросить
          </button>
        )}
      </div>

      {error && <div className="error-box">{error}</div>}
      {notice && <div className="mail-notice">{notice}</div>}

      {composing && (
        <form className="panel mail-compose" onSubmit={(event) => void send(event)}>
          <div className="mail-compose-title">
            <h2>{replyToId ? "Ответить" : "Новое письмо"}</h2>
            <button type="button" className="secondary" onClick={() => setComposing(false)}>
              Закрыть
            </button>
          </div>
          <div className="field">
            <label htmlFor="mail-to">Кому</label>
            <input id="mail-to" type="email" value={toAddress} onChange={(event) => setToAddress(event.target.value)} required />
          </div>
          <div className="field">
            <label htmlFor="mail-subject">Тема</label>
            <input id="mail-subject" value={subject} onChange={(event) => setSubject(event.target.value)} maxLength={998} required />
          </div>
          <div className="field">
            <label htmlFor="mail-body">Сообщение</label>
            <textarea id="mail-body" value={body} onChange={(event) => setBody(event.target.value)} maxLength={12000} required />
          </div>
          <div className="mail-compose-actions">
            <span className="note">Письмо уйдёт с настроенного служебного адреса.</span>
            <button type="submit" disabled={sending}>{sending ? "Отправка…" : "Отправить"}</button>
          </div>
        </form>
      )}

      <div className="mail-workspace">
        <aside className="mail-list" aria-label="Список писем">
          <div className="mail-list-summary">{loading ? "Загрузка…" : `Писем: ${total}`}</div>
          {!loading && items.length === 0 && (
            <div className="mail-empty">По выбранным фильтрам писем нет.</div>
          )}
          {items.map((message) => {
            const correspondent =
              message.direction === "inbound" ? message.from_address : message.to_address;
            return (
              <button
                key={message.id}
                className={`mail-list-item${selectedId === message.id ? " is-selected" : ""}`}
                onClick={() => setSelectedId(message.id)}
              >
                <span className="mail-list-topline">
                  <strong>{correspondent || "Адрес не указан"}</strong>
                  <time>{formatDate(message.message_at)}</time>
                </span>
                <span className="mail-list-subject">{message.subject || "Без темы"}</span>
                <span className="mail-list-preview">{shortText(message.body) || "Пустое письмо"}</span>
                <span className="mail-list-tags">
                  <span>{message.direction === "inbound" ? "Входящее" : "Отправленное"}</span>
                  {message.is_unresolved && <span className="mail-unresolved">Неопределённый email</span>}
                  {message.rfq_id && <span>RFQ-{message.rfq_id}</span>}
                </span>
              </button>
            );
          })}
        </aside>

        <article className="mail-reader">
          {!selected ? (
            <div className="mail-empty">Выберите письмо слева.</div>
          ) : (
            <>
              <header className="mail-reader-header">
                <div>
                  <div className="mail-reader-titleline">
                    <h2>{selected.subject || "Без темы"}</h2>
                    {selected.is_unresolved && (
                      <span className="mail-unresolved">Неопределённый email</span>
                    )}
                  </div>
                  <dl>
                    <div><dt>От:</dt><dd>{selected.from_address || "—"}</dd></div>
                    <div><dt>Кому:</dt><dd>{selected.to_address || "—"}</dd></div>
                    <div><dt>Дата:</dt><dd>{formatDate(selected.message_at)}</dd></div>
                    {selected.rfq_id && <div><dt>Связь:</dt><dd>RFQ-{selected.rfq_id}</dd></div>}
                  </dl>
                </div>
                {canWrite && (
                  <button className="secondary" onClick={() => startCompose(selected)}>
                    Ответить
                  </button>
                )}
              </header>
              {selected.is_unresolved && (
                <div className="mail-unresolved-explanation">
                  Письмо не удалось связать с ранее отправленным RFQ по Message-ID,
                  цепочке ответов или номеру запроса в теме.
                </div>
              )}
              <div className="mail-body">{selected.body || "Письмо не содержит текста."}</div>
              {!!selected.attachments?.length && (
                <div className="mail-attachments">
                  <strong>Вложения</strong>
                  {selected.attachments.map((attachment, index) =>
                    attachment.document_id ? (
                      <button
                        key={`${attachment.filename}-${index}`}
                        className="secondary"
                        onClick={() =>
                          void downloadAttachment(
                            attachment.document_id!,
                            attachment.filename,
                          )
                        }
                        disabled={downloadBusy === attachment.document_id}
                      >
                        {downloadBusy === attachment.document_id ? "Загрузка…" : "Скачать"}
                        {` ${attachment.filename} · ${Math.ceil(attachment.size / 1024)} КБ`}
                      </button>
                    ) : (
                      <span key={`${attachment.filename}-${index}`}>
                        {attachment.filename} · {Math.ceil(attachment.size / 1024)} КБ
                      </span>
                    ),
                  )}
                </div>
              )}
            </>
          )}
        </article>
      </div>
    </section>
  );
}
