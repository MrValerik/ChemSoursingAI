import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { RecipientRead, RFQRead } from "../api/types";

const STATUS_LABELS: Record<string, string> = {
  queued: "ожидает отправки",
  sent: "отправлено",
  error: "ошибка",
};

export default function RfqDispatchPreparation({
  rfq,
  readOnly,
  onGoToSuppliers,
  onSent,
}: {
  rfq: RFQRead;
  readOnly: boolean;
  onGoToSuppliers: () => void;
  onSent: () => void;
}) {
  const [recipients, setRecipients] = useState<RecipientRead[]>([]);
  const [reviewed, setReviewed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const queued = useMemo(
    () => recipients.filter((item) => item.status === "queued"),
    [recipients],
  );
  const queueKey = queued.map((item) => `${item.id}:${item.updated_at}`).join("|");

  const load = async () => {
    try {
      setRecipients(await api.listRecipients(rfq.id));
      setError(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rfq.id]);

  useEffect(() => {
    // Изменился список или статус получателей — RFQ нужно проверить заново.
    setReviewed(false);
  }, [queueKey]);

  const removeRecipient = async (recipient: RecipientRead) => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await api.removeRecipient(rfq.id, recipient.id);
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  };

  const dispatch = async () => {
    if (!reviewed || queued.length === 0) return;
    const names = queued
      .map((item) => item.supplier_company ?? `Поставщик #${item.supplier_id}`)
      .join(", ");
    if (
      !window.confirm(
        `Отправить показанный RFQ ${queued.length} получателям: ${names}? При включённых каналах это реальное внешнее действие.`,
      )
    ) {
      return;
    }

    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await api.dispatchRfq(rfq.id, true);
      const failed = result.filter((item) => item.status === "error");
      setRecipients(result);
      setReviewed(false);
      if (failed.length > 0) {
        setError(
          `Не отправлено: ${failed.length}. Проверьте контакты и настройки каналов.`,
        );
      } else {
        setNotice("RFQ отправлен выбранным поставщикам.");
      }
      onSent();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
      await load();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rfq-dispatch-preparation">
      <section className="panel">
        <div className="tab-toolbar">
          <div>
            <h2>Поставщики</h2>
            <p className="note">
              Получатели, выбранные на предыдущем этапе. В отправку попадут
              только строки со статусом «ожидает отправки».
            </p>
          </div>
          {!readOnly && (
            <button className="secondary" onClick={onGoToSuppliers} type="button">
              Изменить выбор
            </button>
          )}
        </div>

        {recipients.length === 0 ? (
          <div className="rfq-recipient-empty">
            <p className="note">Поставщики для отправки ещё не выбраны.</p>
            {!readOnly && (
              <button onClick={onGoToSuppliers} type="button">
                Выбрать поставщиков
              </button>
            )}
          </div>
        ) : (
          <div className="rfq-recipient-list">
            {recipients.map((recipient) => (
              <div className="rfq-recipient-row" key={recipient.id}>
                <div>
                  <strong>
                    {recipient.supplier_company ?? `Поставщик #${recipient.supplier_id}`}
                  </strong>
                  <span className="note">
                    {recipient.channel === "email" ? "Email" : "WhatsApp"}
                  </span>
                </div>
                <div className="rfq-recipient-status">
                  <span
                    className={`badge ${
                      recipient.status === "sent"
                        ? "tone-ok"
                        : recipient.status === "error"
                          ? "tone-warn"
                          : "tone-neutral"
                    }`}
                  >
                    {STATUS_LABELS[recipient.status] ?? recipient.status}
                  </span>
                  {recipient.note && <span className="note">{recipient.note}</span>}
                  {!readOnly && recipient.status === "queued" && (
                    <button
                      className="secondary btn-small"
                      disabled={busy}
                      onClick={() => void removeRecipient(recipient)}
                      type="button"
                    >
                      Убрать
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="panel rfq-preview-panel">
        <div className="tab-toolbar">
          <div>
            <h2>Предпросмотр RFQ</h2>
            <p className="note">
              Это точный английский текст первого сообщения, который получат
              выбранные поставщики.
            </p>
          </div>
          <span className="badge tone-neutral">получателей: {queued.length}</span>
        </div>

        <div className="rfq-preview-message">
          <div className="rfq-preview-subject">
            <span>Тема Email</span>
            <strong>
              [RFQ-{rfq.id}] {rfq.rfq_subject ?? "Request for quotation"}
            </strong>
          </div>
          <div className="rfq-preview-body">
            <span>Сообщение</span>
            <div>{rfq.rfq_body ?? "Текст RFQ временно недоступен."}</div>
          </div>
        </div>

        {notice && <p className="success-note">{notice}</p>}
        {error && <p className="error">{error}</p>}

        {!readOnly && queued.length > 0 && (
          <div className="rfq-preview-confirmation">
            <label>
              <input
                checked={reviewed}
                onChange={(event) => setReviewed(event.target.checked)}
                type="checkbox"
              />
              Я проверил RFQ, каналы и список получателей
            </label>
            <button
              disabled={busy || !reviewed}
              onClick={() => void dispatch()}
              type="button"
            >
              {busy ? "Отправка…" : `Отправить RFQ (${queued.length})`}
            </button>
          </div>
        )}
      </section>
    </div>
  );
}
