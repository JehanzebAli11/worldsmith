-- Worldsmith preflight probe v1 (fixed, reviewed, READ-ONLY; ADR 0011). Sent with run_code in
-- Edit mode before every build when the experiment declares `preflight`. It only reads: no
-- property, attribute or parent is set, nothing is created or destroyed. It prints one line,
-- WS_PREFLIGHT|<json>, which the runner checks (runner/preflight.py):
--   ws_place = "worldsmith-lab" on Workspace (set once by the owner in the saved place);
--   StarterPlayer.LoadCharacterAppearance false; Players.CharacterAutoLoads true;
--   no StarterPlayer.StarterCharacter; exactly one SpawnLocation outside Workspace.Worldsmith
--   (the Baseplate template's); no script (any LuaSourceContainer) in ServerScriptService,
--   StarterPlayerScripts, StarterCharacterScripts or StarterGui unless Worldsmith owns it (name
--   starts with "Worldsmith" AND attribute ws_owned == true); not running (Edit mode).
local HttpService = game:GetService("HttpService")
local RunService = game:GetService("RunService")
local Players = game:GetService("Players")
local StarterPlayer = game:GetService("StarterPlayer")
local StarterGui = game:GetService("StarterGui")
local ServerScriptService = game:GetService("ServerScriptService")
local MAX_LIST = 16

local marker = workspace:GetAttribute("ws_place")
local out = {
    schema = "worldsmith.preflight_probe/0.1",
    game_name = game.Name,
    place_id = game.PlaceId,
    is_running = RunService:IsRunning(),
    ws_place = (marker == nil) and "<none>" or tostring(marker),
    load_character_appearance = StarterPlayer.LoadCharacterAppearance,
    character_auto_loads = Players.CharacterAutoLoads,
    starter_character = StarterPlayer:FindFirstChild("StarterCharacter") ~= nil,
    spawns_outside = 0,
    spawns_outside_list = {},
    spawns_inside = 0,
    foreign_scripts = 0,
    foreign_scripts_list = {},
    owned_scripts = 0,
}

local root = workspace:FindFirstChild("Worldsmith")
for _, d in ipairs(workspace:GetDescendants()) do
    if d:IsA("SpawnLocation") then
        if root and d:IsDescendantOf(root) then
            out.spawns_inside += 1
        else
            out.spawns_outside += 1
            if #out.spawns_outside_list < MAX_LIST then
                local p = d.Position
                table.insert(out.spawns_outside_list, {
                    path = d:GetFullName(), pos = string.format("%.2f,%.2f,%.2f", p.X, p.Y, p.Z),
                    neutral = d.Neutral, enabled = d.Enabled,
                })
            end
        end
    end
end

local containers = {
    ServerScriptService,
    StarterPlayer:FindFirstChildOfClass("StarterPlayerScripts"),
    StarterPlayer:FindFirstChildOfClass("StarterCharacterScripts"),
    StarterGui,
}
for i = 1, 4 do
    local c = containers[i]
    if c then
        for _, d in ipairs(c:GetDescendants()) do
            if d:IsA("LuaSourceContainer") then
                if string.sub(d.Name, 1, 10) == "Worldsmith" and d:GetAttribute("ws_owned") == true then
                    out.owned_scripts += 1
                else
                    out.foreign_scripts += 1
                    if #out.foreign_scripts_list < MAX_LIST then
                        table.insert(out.foreign_scripts_list, { path = d:GetFullName(), class = d.ClassName })
                    end
                end
            end
        end
    end
end

print("WS_PREFLIGHT|" .. HttpService:JSONEncode(out))
