UPDATE escalations
SET suggested_reply = NULL
WHERE note ILIKE 'Отправитель первого письма не сопоставлен с ранее выбранным поставщиком.%'
  AND suggested_reply =
      'Thank you for your message. Your question requires internal review. We will get back to you shortly.';
