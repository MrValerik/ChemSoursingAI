import type { EchemiSearch } from "../api/types";
import EchemiVerification from "./EchemiVerification";
import "./echemi.css";

const labels: Record<string,string> = {
  queued:"В очереди", running:"Идёт поиск", completed:"Завершён", partial:"Частичный результат",
  blocked:"Проверка Echemi не пройдена", failed:"Ошибка", read:"Карточка прочитана",
  pending:"Ожидает чтения", reading:"Читаем карточку", not_read:"Карточка не прочитана", redirected:"Карточка перенаправлена",
};
const external = (url?: string | null) => url?.startsWith("https://www.echemi.com/") ? url : undefined;

export function EchemiResults({ selected, allowVerification = false }: { selected: EchemiSearch; allowVerification?: boolean }) {
  return <>
      <h2>Результаты: {selected.query}</h2>
      {selected.status === "running" && allowVerification && <EchemiVerification key={selected.id} searchId={selected.id} />}
      {["queued", "running"].includes(selected.status) ?
        <div className="echemi-loading" role="status" aria-live="polite">
          <div className="echemi-loading-heading">
            <span className="echemi-loading-icon" aria-hidden="true"><span className="loading-spinner" /></span>
            <div>
              <strong>{selected.status === "queued" ? "Запрос в очереди" : "Ищем товары в Echemi"}</strong>
              <p>{selected.status === "queued"
                ? "Поиск начнётся, когда завершится предыдущий запрос."
                : selected.message || "Открываем Echemi и читаем карточки. Это может занять несколько минут."}</p>
            </div>
          </div>
          <div className="echemi-loading-track" aria-hidden="true"><span /></div>
          <small>Таблица заполняется по мере поиска. Можно перейти к другим запросам и вернуться позже.</small>
        </div> :
        <p role="status">{labels[selected.status] || selected.status}. {selected.message}</p>}
      <p>Цены опубликованы на площадке и не являются подтверждённой котировкой. Заявленная роль продавца требует проверки.</p>
      {!selected.results.length ? (!["queued","running"].includes(selected.status) && <p>Сохранённых товаров нет.</p>) :
      <div className="echemi-table"><table><thead><tr>
        <th>Товар / компания</th><th>Цена из выдачи</th><th>Характеристики</th><th>Контакты</th><th>Источник и состояние</th>
      </tr></thead><tbody>{selected.results.map((r,i)=><tr key={r.product_url || i}>
        <td><strong>{r.title || "Название не указано"}</strong><p>{r.seller_name || "Компания не указана"}</p>
          {external(r.seller_url) && <a href={r.seller_url!} target="_blank" rel="noreferrer">Страница компании</a>}</td>
        <td>{r.price_text || "Цена не опубликована"}<small>{r.warnings?.join(" ")}</small></td>
        <td>{r.detail ? Object.entries(r.detail.fields).filter(([,v])=>v.length).map(([k,v])=><p key={k}>
          <b>{{cas:"CAS",purity:"Чистота",grade:"Сорт",packaging:"Упаковка",minimum_order:"MOQ",address:"Адрес",contact_person:"Контактное лицо"}[k] || k}: </b>
          {v.map(x=>x.value).join("; ")}</p>) :
          ["pending","reading"].includes(r.detail_status) && selected.status === "running" ? "Характеристики появятся после чтения карточки" : "Карточка не прочитана"}</td>
        <td>{r.detail?.contacts.length ? r.detail.contacts.map((c,n)=><p key={n}>{c.value}
          <small>{c.owner==="platform_echemi"?"Контакт площадки Echemi":"Принадлежность поставщику не подтверждена"}</small></p>) :
          !r.detail ? "Контакты ещё не проверены" : "Публичные контакты не найдены"}</td>
        <td>{labels[r.detail_status] || r.detail_status}<p>
          {external(r.product_url) && <a href={r.product_url!} target="_blank" rel="noreferrer">Открыть карточку ↗</a>}</p>
          <small>{new Date(r.detail?.observed_at || r.observed_at).toLocaleString("ru-RU")}</small>
          <details><summary>Исходный текст и доказательства</summary>
            <h4>Выдача</h4><pre>{r.source_text}</pre>
            {r.detail && <><h4>Карточка</h4><pre>{r.detail.source_text}</pre></>}
          </details></td>
      </tr>)}</tbody></table></div>}
  </>;
}
