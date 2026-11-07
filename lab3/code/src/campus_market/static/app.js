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
        form.querySelector(".query-result").textContent = JSON.stringify(result, null, 2);
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
