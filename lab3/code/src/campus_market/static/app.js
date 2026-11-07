"use strict";

const csrf = () => document.querySelector('meta[name="csrf-token"]').content;
function notice(message) {
  const panel = document.querySelector("#notice");
  panel.textContent = message;
  panel.hidden = false;
  panel.scrollIntoView({block: "nearest"});
}
async function api(url, method = "POST", values = {}) {
  const headers = {"X-CSRF-Token": csrf()};
  const options = {method, headers, credentials: "same-origin"};
  if (method !== "GET") {
    if (values instanceof FormData) options.body = values;
    else { headers["Content-Type"] = "application/json"; options.body = JSON.stringify(values); }
  }
  const response = await fetch(url, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error?.message || "请求失败，请刷新状态后重试");
  return result;
}
function values(form) {
  const result = Object.fromEntries(new FormData(form));
  for (const field of form.querySelectorAll("[data-boolean]")) result[field.name] = field.value === "true";
  return result;
}
function displayQuery(panel, result) {
  panel.replaceChildren();
  if ("amount_cents" in result) {
    const titles = [["首次发布商品", result.first_published], ["完成交易", result.completed_trades],
      ["成交金额", new Intl.NumberFormat("zh-CN", {style:"currency",currency:"CNY"}).format(result.amount_cents/100)]];
    const grid = document.createElement("div"); grid.className = "grid";
    for (const [title, value] of titles) {
      const card = document.createElement("section"); card.className = "card";
      const heading = document.createElement("h3"); heading.textContent = title;
      const number = document.createElement("strong"); number.className = "price"; number.textContent = value;
      card.append(heading, number); grid.append(card);
    }
    panel.append(grid); return;
  }
  const summary = document.createElement("p"); summary.textContent = `共 ${result.total} 条记录，当前第 ${result.page} 页`;
  panel.append(summary);
  const table = document.createElement("table");
  const columns = [["created_at","时间"],["actor_id","操作人编号"],["action","动作"],
    ["object_id","对象编号"],["before_state","原状态"],["after_state","新状态"],
    ["result","结果"],["reason","原因"]];
  const header = table.createTHead().insertRow();
  for (const [,title] of columns) { const cell = document.createElement("th"); cell.textContent=title; header.append(cell); }
  const rows = table.createTBody();
  for (const record of result.items) {
    const row = rows.insertRow();
    for (const [key] of columns) row.insertCell().textContent = record[key] ?? "—";
  }
  panel.classList.add("table-scroll"); panel.append(table);
}
async function confirmAction(message, safeLabel) {
  const dialog = document.querySelector("#confirmation");
  const keep = document.querySelector("#keep-action");
  const accept = document.querySelector("#confirm-action");
  document.querySelector("#confirm-message").textContent = message;
  keep.textContent = safeLabel || "暂不操作";
  dialog.returnValue = "cancel";
  return new Promise(resolve => {
    const done = answer => { dialog.returnValue = answer; dialog.close(); };
    keep.onclick = () => done("cancel");
    accept.onclick = () => done("accept");
    dialog.onclose = () => resolve(dialog.returnValue === "accept");
    dialog.showModal();
    keep.focus();
  });
}
async function busy(button, operation) {
  button.disabled = true;
  try { await operation(); }
  catch (error) { notice(error.message || "连接中断，请查询原请求结果或刷新状态"); }
  finally { button.disabled = false; }
}
for (const form of document.querySelectorAll("form[data-api],form[data-upload],form[data-query],form[data-api-dynamic]")) {
  form.addEventListener("submit", event => {
    event.preventDefault();
    const button = form.querySelector("button");
    busy(button, async () => {
      if (form.dataset.query) {
        const parameters = new URLSearchParams(values(form));
        for (const [key, value] of [...parameters]) if (!value) parameters.delete(key);
        const result = await api(form.dataset.query + "?" + parameters, "GET");
        displayQuery(form.querySelector(".query-result"), result);
        return;
      }
      let url = form.dataset.api || form.dataset.upload;
      const data = form.dataset.upload ? new FormData(form) : values(form);
      if (form.dataset.apiDynamic === "moderate") {
        url = "/api/admin/products/" + encodeURIComponent(data.product_id) + "/moderate";
        delete data.product_id;
      }
      const result = await api(url, form.dataset.method || "POST", data);
      if (form.dataset.created === "product") location.href = "/products/" + result.id + "/edit";
      else location.href = form.dataset.next || location.href;
    });
  });
}
for (const button of document.querySelectorAll("button[data-action]")) {
  button.addEventListener("click", async () => {
    if (button.dataset.confirm && !await confirmAction(button.dataset.confirm, button.dataset.safeLabel)) return;
    busy(button, async () => {
      const data = button.dataset.version ? {version: button.dataset.version} : {};
      const result = await api(button.dataset.action, button.dataset.method || "POST", data);
      if (button.dataset.action.endsWith("/cancel")) sessionStorage.removeItem("reserve:" + result.product_id);
      location.href = button.dataset.next || location.href;
    });
  });
}
for (const button of document.querySelectorAll("button[data-reserve]")) {
  button.addEventListener("click", () => busy(button, async () => {
    const id = button.dataset.reserve;
    const storageKey = "reserve:" + id;
    let key = sessionStorage.getItem(storageKey);
    if (!key) { key = crypto.randomUUID(); sessionStorage.setItem(storageKey, key); }
    const result = await api("/api/products/" + id + "/reservations", "POST", {request_key: key});
    location.href = "/reservations/" + result.reservation.id;
  }));
}
for (const button of document.querySelectorAll("button[data-recover]")) {
  button.addEventListener("click", () => busy(button, async () => {
    const key = sessionStorage.getItem("reserve:" + button.dataset.recover);
    if (!key) throw new Error("本浏览器没有保留该商品的请求标识，请查看我的预约");
    const result = await api("/api/requests/" + encodeURIComponent(key), "GET");
    location.href = "/reservations/" + result.current.id;
  }));
}
