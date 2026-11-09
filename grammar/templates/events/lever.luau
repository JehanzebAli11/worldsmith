-- Worldsmith lever (fixed template): touch its zone or trigger its prompt; latches on once.
local lever = script.Parent
local lib = require(lever:FindFirstAncestor("Worldsmith"):WaitForChild("ws_lib"))
local done = false
local function activate(character)
    if done then
        return
    end
    done = true
    lib.emit("LEVER_ACTIVATED", lever.Name, character)
    local handle = lever:FindFirstChild("handle")
    if handle then
        handle.Color = Color3.fromRGB(75, 151, 75)
    end
    lever:SetAttribute("ws_active", true)
end
lever:WaitForChild("zone").Touched:Connect(function(hit)
    local character = lib.characterOf(hit)
    if character then
        activate(character)
    end
end)
local prompt = lever:WaitForChild("handle"):WaitForChild("prompt")
prompt.Triggered:Connect(function(player)
    activate(player.Character)
end)
