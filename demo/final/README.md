# demo/final: final looks (simulated)



The images and recipes used by the app's "final look" card, exported by `tools/export_final_looks.py` from the offline run folders (not in this repository). Every image is labelled *Simulated* on screen, with the note "simulated picture, not an actual makeup result" under it. Recipe cards carry no brand names or official shade codes. File checksums are in `SHA256SUMS.txt`; source checksums are in `source_sha256` inside `recipes.json`.

| Look | Photo source | Credit text shown on screen |
|---|---|---|
| albinism_F / albinism_M | Pexels 5273693 (modification allowed) | Photo: Pexels (Pexels License, modification allowed) |
| vitiligo_M | Pexels 20551205 (modification allowed) | Photo: Pexels (Pexels License, modification allowed) |
| vitiligo_F | **Not an original photo**: a side-view original from Unsplash (uCt9uA9r1Rw), turned into a frontal illustration by an AI image tool, then local layers, then the official blush and eyeshadow | Photo: simulated, an AI-generated frontal illustration, not the person's original photo (original: Unsplash, modification allowed) |

About vitiligo_F:
- The frontal picture is AI-generated; whether the face shape and outline are faithful to the original is not verified, so it must not be taken as the person's photo.
- Public text on the app screens uses neutral wording; the file names in this folder keep internal labels.
- The full source chain is also in `source_note` of `vitiligo_F` in `recipes.json`.
