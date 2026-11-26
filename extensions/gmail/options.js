const updateCheck = document.getElementById("update-check");

chrome.storage.local.get("updateCheck").then((stored) => { updateCheck.checked = stored.updateCheck !== false; });
updateCheck.addEventListener("change", () => chrome.storage.local.set({ updateCheck: updateCheck.checked }));
