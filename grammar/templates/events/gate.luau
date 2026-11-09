-- Worldsmith gate (fixed template): opens once its lever's ws_active becomes true.
local gate = script.Parent
local root = gate:FindFirstAncestor("Worldsmith")
local lib = require(root:WaitForChild("ws_lib"))
local lever = root:WaitForChild(gate:GetAttribute("ws_lever"))
local function sync()
    if lever:GetAttribute("ws_active") == true and gate:GetAttribute("ws_open") ~= true then
        gate:SetAttribute("ws_open", true)
        gate.CanCollide = false
        gate.Transparency = 0.8
        lib.emit("GATE_OPENED", gate.Name, nil)
    end
end
lever:GetAttributeChangedSignal("ws_active"):Connect(sync)
sync()
