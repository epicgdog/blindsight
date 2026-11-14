# Blindsight pitch script (target under 3:00 at 150 words per minute)

> Stage notes and headings are not counted. Say only measured numbers; none are used here.

## 0:00 to 0:25 Problem
Blindsight lets blind and low-vision people tour a property independently, guided by voice, using CoStar 3D data.

If you cannot see, you cannot browse listings. You cannot glance at photos, and walking through an unfamiliar space alone is risky. So someone has to go with you, or you go without knowing what is there.

## 0:25 to 0:50 Insight
CoStar already holds a detailed 3D record of a huge number of spaces. It was built for sighted buyers. Nobody has turned it into guidance for people who cannot see.

## 0:50 to 2:00 Live demo
[Volunteer is blindfolded, standing on the taped spot, holding the phone upright.]

This is our main lobby, scanned in 3D. The phone camera streams to our laptop. Listen.

[Cue plays: "You are in the main lobby."]

The laptop matched the live view against the scan and locked on. You can see the badge, the match score, and the geometric inliers that prove it.

[Volunteer turns toward the staged chair. Cue plays: "Chair, ahead, close."]

That is object detection running live. The boxes you see are what the voice just described.

[Volunteer responds to the voice, for example reaching toward the chair.]

The voice told a person who cannot see where they are and what is in front of them, in real time.

## 2:00 to 2:35 How it works and roadmap
Here is the whole system. Phone video goes to the laptop. We detect objects, match the frame against the Matterport pano with geometric verification, and speak short cues back to the phone.

Today this covers one scan point. Next, scan positions become nodes in a graph, so we can guide room to room. Then Vexcel imagery guides someone from the street to the door, and we hand off to the indoor tour. Those two are roadmap, not built.

## 2:35 to 3:00 Why CoStar wins
This gives CoStar a new audience on its listing platforms, and a feature competitors cannot copy without this data.

Blindsight lets blind and low-vision people tour a property independently, guided by voice, using CoStar 3D data.
