-- WS-03 route (a) feasibility probe: a P0-style scripted controller run as a server script via
-- run_script_in_play_mode. Same code for every F1 scene; waypoints in the scene frame.
-- Not the WS-04 controller and not an M0 result.
local Players = game:GetService("Players")
local root = workspace:WaitForChild("Worldsmith")
local origin = root:GetAttribute("ws_origin")
local WAYPOINTS = {
    { "corridor", 0, 0, 20 },
    { "ledge_entry", -6, 0, 24 },
    { "lever_zone", -13, 0, 24 },
    { "corridor_mid", 0, 0, 24 },
    { "before_gate", 0, 0, 31 },
    { "past_gate", 0, 0, 35.5 },
    { "bridge", 0, 0, 40 },
    { "landing", 0, 0, 55 },
    { "goal", 0, 0, 60 },
}
local T_ATTEMPT = 45
local T_WAYPOINT = 10
local player = Players:GetPlayers()[1] or Players.PlayerAdded:Wait()
player.RespawnLocation = root:WaitForChild("spawn")
player:LoadCharacter()
local char = player.Character or player.CharacterAdded:Wait()
local hum = char:WaitForChild("Humanoid")
local hrp = char:WaitForChild("HumanoidRootPart")
task.wait(1)
local died = false
hum.Died:Connect(function()
    died = true
end)
local function rel(p)
    local q = p - origin
    return string.format("%.2f,%.2f,%.2f", q.X, q.Y, q.Z)
end
local t0 = os.clock()
print("WS_PROBE|start|pos=" .. rel(hrp.Position))
local results = {}
for i, w in ipairs(WAYPOINTS) do
    if died or os.clock() - t0 > T_ATTEMPT then
        break
    end
    local done, reached = false, false
    local c = hum.MoveToFinished:Connect(function(r)
        done = true
        reached = r
    end)
    hum:MoveTo(origin + Vector3.new(w[2], w[3], w[4]))
    local tw = os.clock()
    while not done and not died and os.clock() - tw < T_WAYPOINT do
        task.wait(0.05)
    end
    c:Disconnect()
    local line = string.format("WS_PROBE|wp=%d|%s|reached=%s|died=%s|t=%.2f|pos=%s", i, w[1], tostring(reached), tostring(died), os.clock() - t0, rel(hrp.Position))
    print(line)
    table.insert(results, line)
end
task.wait(3)
local summary = string.format("WS_PROBE|end|died=%s|t=%.2f|lever=%s|gate=%s", tostring(died), os.clock() - t0, tostring(root.lever_1:GetAttribute("ws_active")), tostring(root.gate_1:GetAttribute("ws_open")))
print(summary)
return summary
