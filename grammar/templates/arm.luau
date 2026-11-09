-- Worldsmith arm (fixed template): set the run nonce that the event scripts print.
local root = workspace:FindFirstChild("Worldsmith")
if not root then
    error("WS_ARM_FAILED|no Workspace.Worldsmith")
end
root:SetAttribute("ws_nonce", {{nonce:nonce}})
print("WS_ARMED|" .. root:GetAttribute("ws_nonce"))
