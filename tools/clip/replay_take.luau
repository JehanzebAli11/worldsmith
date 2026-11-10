-- On-camera replay used for the episode-1 footage (11 Nov 2026). NOT a logged run.
-- Why: run_script_in_play_mode (the logged path) does not draw the client view in Studio's
-- viewport, so the logged runs could not be filmed. This Script was placed temporarily in
-- ServerScriptService and run with Studio's normal Play (start_stop_play), then removed; the
-- place was not saved. Same waypoints and MoveTo-only movement as the frozen P0 controller
-- (no jump, no teleport, no WalkSpeed change); no result logging. Scenes: F1-A (bridge 6) and
-- F1-A-fix (bridge 16), set on the built tree to the same sizes and positions as the logged builds.
local T_WAYPOINT = 10
local TICK = 0.05
local WAYPOINTS = {
    { "corridor", 0, 0, 20 }, { "ledge_entry", -6, 0, 24 }, { "lever_zone", -13, 0, 24 },
    { "corridor_mid", 0, 0, 24 }, { "before_gate", 0, 0, 31 }, { "past_gate", 0, 0, 35.5 },
    { "bridge", 0, 0, 40 }, { "landing", 0, 0, 55 }, { "goal", 0, 0, 60 },
}
local Players = game:GetService("Players")
local root = workspace:WaitForChild("Worldsmith")
local origin = root:GetAttribute("ws_origin")
local spawn = root:FindFirstChild("spawn")
root:SetAttribute("ws_nonce", "1650cbccfc05c219") -- fa6b8e3996e68e8e for the F1-A-fix take
local player = Players:GetPlayers()[1] or Players.PlayerAdded:Wait()
local old = player.Character or player.CharacterAdded:Wait()
task.wait(1)
player.RespawnLocation = spawn
player:LoadCharacter()
local char = player.Character
while char == old or not char do task.wait(0.05); char = player.Character end
local hum = char:WaitForChild("Humanoid")
char:WaitForChild("HumanoidRootPart")
task.wait(1.5)
for _, w in ipairs(WAYPOINTS) do
    if hum.Health <= 0 or char.Parent == nil then break end
    local done = false
    local conn = hum.MoveToFinished:Connect(function() done = true end)
    hum:MoveTo(origin + Vector3.new(w[2], w[3], w[4]))
    local t = os.clock()
    while not done and hum.Health > 0 and os.clock() - t < T_WAYPOINT do task.wait(TICK) end
    conn:Disconnect()
end
print("WS_VISUAL_TAKE_DONE|" .. tostring(root:GetAttribute("ws_scene_hash")) .. "|died=" .. tostring(hum.Health <= 0))

-- Camera (temporary LocalScript in StarterPlayerScripts, also removed afterwards):
-- local eye = o + Vector3.new(-36, 52, 33); local at = o + Vector3.new(-4, -4, 33)
-- cam.CameraType = Scriptable; FieldOfView = 50; cam.CFrame = CFrame.lookAt(eye, at) every frame.
