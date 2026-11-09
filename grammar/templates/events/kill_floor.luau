-- Worldsmith kill floor (fixed template): prints KILL_FLOOR_TOUCHED once per character, then
-- ends that character (Health = 0).
local floor = script.Parent
local lib = require(floor:FindFirstAncestor("Worldsmith"):WaitForChild("ws_lib"))
local seen = setmetatable({}, { __mode = "k" })
floor.Touched:Connect(function(hit)
    local character, humanoid = lib.characterOf(hit)
    if character and not seen[character] then
        seen[character] = true
        lib.emit("KILL_FLOOR_TOUCHED", floor.Name, character)
        humanoid.Health = 0
    end
end)
