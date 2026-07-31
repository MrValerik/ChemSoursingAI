import { useCallback, useEffect, useState } from "react";
import { api, ApiError, userErrorMessage } from "../api/client";
import type { SupplierDocumentRead } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { Icon, Toast } from "./ui";

const KIND_LABELS: Record<string, string> = {
  coa: "Паспорт качества (CoA)",
  tds: "Спецификация (TDS)",
  msds: "Паспорт безопасности",
  other: "Другой документ",
};

const TEXT_STATUS_LABELS: Record<string, string> = {
  stored: "Текст ещё не извлечён",
  extracted: "Текст извлечён",
  needs_ocr: "Скан без текстового слоя",
  unsupported: "Формат не поддерживается",
  failed: "Не удалось прочитать",
};

const VERIFICATION_LABELS: Record<string, string> = {
  confirmed: "Документ подтверждён",
  needs_review: "Нужна ручная проверка",
  rejected: "Документ отклонён",
  unavailable: "Проверка не выполнена",
};

const verificationTone = (status: string | undefined) => {
  if (status === "confirmed") return "tone-ok";
  if (status === "rejected") return "tone-danger";
  if (status === "needs_review") return "tone-warn";
  return "tone-neutral";
};

const formatSize = (bytes: number) =>
  bytes >= 1024 * 1024
    ? `${(bytes / 1024 / 1024).toFixed(1)} МБ`
    : `${Math.max(1, Math.round(bytes / 1024))} КБ`;

export default function DocumentsSection({ rfqId }: { rfqId: number }) {
  const { user } = useAuth();
  const readOnly = user?.role === "auditor";
  const [documents, setDocuments] = useState<SupplierDocumentRead[]>([]);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setDocuments(await api.listRfqDocuments(rfqId));
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : String(caught));
    }
  }, [rfqId]);

  useEffect(() => {
    void load();
  }, [load]);

  const verify = async (documentId: number) => {
    setBusyId(documentId);
    setError(null);
    setNotice(null);
    try {
      const updated = await api.verifyDocument(documentId);
      setDocuments((current) =>
        current.map((item) => (item.id === documentId ? updated : item)),
      );
      setNotice(
        `Проверка завершена: ${
          VERIFICATION_LABELS[updated.verification?.status ?? ""] ??
          "результат сохранён"
        }.`,
      );
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? userErrorMessage(caught.message)
          : String(caught),
      );
    } finally {
      setBusyId(null);
    }
  };

  if (documents.length === 0) {
    return (
      <div className="panel">
        <h2>Документы поставщиков</h2>
        {error && <p className="error">{error}</p>}
        <p className="note">
          Пока нет ни одного документа. Файлы появляются здесь автоматически,
          когда поставщик присылает паспорт качества или спецификацию в ответ
          на письмо.
        </p>
      </div>
    );
  }

  return (
    <div className="panel">
      <h2>Документы поставщиков</h2>
      <p className="note">
        Проверка сверяет документ с веществом запроса и подтверждает выводы
        только дословными цитатами из самого файла. Она не заменяет решение
        специалиста о пригодности партии.
      </p>
      {error && <p className="error">{error}</p>}
      {notice && <Toast message={notice} onClose={() => setNotice(null)} />}

      <div className="document-list">
        {documents.map((document) => {
          const verification = document.verification;
          const status = verification?.status;
          return (
            <article className="document-card" key={document.id}>
              <div className="document-card-head">
                <div>
                  <strong>{document.filename}</strong>
                  <small>
                    {KIND_LABELS[document.kind] ?? document.kind} ·{" "}
                    {formatSize(document.size_bytes)}
                    {document.page_count ? ` · ${document.page_count} стр.` : ""}
                  </small>
                </div>
                <div className="document-card-actions">
                  {status && (
                    <span className={`badge ${verificationTone(status)}`}>
                      {VERIFICATION_LABELS[status] ?? status}
                    </span>
                  )}
                  <a
                    className="secondary button-like"
                    href={api.documentFileUrl(document.id)}
                  >
                    <Icon name="save" size={14} />
                    Скачать
                  </a>
                  {!readOnly && document.text_status === "extracted" && (
                    <button
                      className="secondary"
                      disabled={busyId === document.id}
                      onClick={() => void verify(document.id)}
                      type="button"
                    >
                      {busyId === document.id
                        ? "Проверка…"
                        : status
                          ? "Проверить заново"
                          : "Проверить документ"}
                    </button>
                  )}
                </div>
              </div>

              {document.text_status !== "extracted" && (
                <p className="qualification-warning">
                  {TEXT_STATUS_LABELS[document.text_status] ??
                    document.text_status}
                  {document.extraction_error
                    ? `: ${document.extraction_error}`
                    : ""}
                  {document.text_status === "needs_ocr"
                    ? ". Проверить такой документ можно только вручную, пока не подключено распознавание сканов."
                    : ""}
                </p>
              )}

              {verification && (
                <div className="document-verification">
                  <p>{verification.gate_reason}</p>
                  {verification.reason && (
                    <p className="note">{verification.reason}</p>
                  )}
                  <dl className="document-facts">
                    <dt>CAS в запросе</dt>
                    <dd>{verification.expected_cas || "—"}</dd>
                    <dt>CAS в документе</dt>
                    <dd>
                      {verification.cas_in_document?.length
                        ? verification.cas_in_document.join(", ")
                        : "не найден"}
                    </dd>
                  </dl>

                  {verification.accepted_claims?.length > 0 && (
                    <details className="trace-subdetails" open>
                      <summary>
                        Подтверждено цитатами (
                        {verification.accepted_claims.length})
                      </summary>
                      <ul className="document-claims">
                        {verification.accepted_claims.map((claim, index) => (
                          <li key={`ok-${document.id}-${index}`}>
                            <strong>{claim.claim_value}</strong>
                            <blockquote>«{claim.quote}»</blockquote>
                          </li>
                        ))}
                      </ul>
                    </details>
                  )}

                  {verification.rejected_claims?.length > 0 && (
                    <details className="trace-subdetails">
                      <summary>
                        Отклонено проверкой цитат (
                        {verification.rejected_claims.length})
                      </summary>
                      <ul className="document-claims rejected">
                        {verification.rejected_claims.map((claim, index) => (
                          <li key={`bad-${document.id}-${index}`}>
                            <strong>{claim.claim_value}</strong>
                            <small>{claim.rejection_reason}</small>
                          </li>
                        ))}
                      </ul>
                    </details>
                  )}

                  {verification.red_flags?.length > 0 && (
                    <ul className="document-flags">
                      {verification.red_flags.map((flag, index) => (
                        <li key={`flag-${document.id}-${index}`}>{flag}</li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
            </article>
          );
        })}
      </div>
    </div>
  );
}
