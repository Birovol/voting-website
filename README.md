# Vote Teacher

Local Flask app "Vote Teacher" — lightweight voting app with admin panel.

Setup

1. Create virtualenv and install requirements:

```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1; pip install -r requirements.txt
```

2. Run the app:

```powershell
python app.py
```

Notes
- Upload limit is 5MB. Images will be resized automatically to reasonable dimensions.
 - There is no fixed upload limit configured (uploads are accepted), but images will be resized automatically to reasonable dimensions on upload.
- If you try to upload very large files, you may see an error flash telling you to resize the image.
- The app prevents multiple votes per person by assigning a `voter_id` cookie and storing it in the DB. This is basic protection and can be bypassed by clearing cookies or changing devices.

Admin
- Login at `/admin/login` using default credentials `admin` / `admin`.
- Settings available at `/admin/settings` (change title, banner, header image, language).
- Edit teachers at the admin panel.

Enhancements
- Confetti and fullscreen celebration modal added.
- Responsive design with Bootstrap, previews, and Chart.js statistics.
