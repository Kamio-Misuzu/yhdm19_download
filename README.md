## 🌸 Sakura Anime Download Script

### Main Interface
<img width="600" height="400" alt="image" src="https://github.com/user-attachments/assets/740d75c2-762b-480f-9145-4cd6f8b935b4" />

### Instructions
- Currently only supports videos from yhdm19.cc website. For example: Enter "https://www.yhdm19.cc/index.php/vod/play/id/30800/sid/1/nid/1.html" in the "Video URL" field to automatically search for the title and episode list
- Then click on the episode number you want to download and modify the save path to start downloading
- The downloaded files are "ts" video files from Sakura Anime. FFmpeg is required if you want to convert them to mp4 format
- The script extracts video URLs using four methods: "player_aaaa", "iframe src", "video player configuration", and "finding m3u8 address"
- For this website, video segments from Source A exist in the master playlist, while other sources mostly use sub-playlists
