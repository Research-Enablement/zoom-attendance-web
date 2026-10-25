# Zoom Attendance Dashboards (web)

**Open the page: https://research-enablement.github.io/zoom-attendance-web/**

A web page that turns Zoom attendance exports into attendance dashboards, with nothing to install. Built at the University of Illinois Urbana-Champaign for any campus unit that runs recurring Zoom sessions.

It runs the [zoom-attendance-template](https://github.com/Research-Enablement/zoom-attendance-template) pipeline inside your browser. **Nothing you choose is uploaded anywhere.** Files are read into the browser tab's memory, and you download the results.

## How to use it

Open [the page](https://research-enablement.github.io/zoom-attendance-web/) in Chrome, Edge or Firefox. The first visit takes about 20 seconds to load.

1. **Attendance history.**
   - First time for your department: click **Start a new history** and type your department name.
   - After that: load the history file you downloaded last time.
2. **Meeting types.** Add the kinds of meetings you track, for example "Data Workshop". For each one, say whether sessions have a topic and presenters. You can rename or hide them later.
3. **Add a meeting.** Pick the meeting type, choose the Zoom file and click **Read file**. Then:
   - **Match these attendees:** anyone who joined without an email, under a Zoom name the history hasn't seen, is listed here. For each one, pick who it is or add them as a new person. The session can't be saved until everyone is matched. The Zoom name is remembered, so next time they match on their own.
   - **New people (optional):** people added from their email can be given a department and status, so the charts show them under the right college.
   - **Session details:** topic, presenters, and how long the meeting was.
   - Click **Save session**.
4. **Dashboards.** Show a session page or the overview across meeting types, and download them as .html files.
5. **Save.** Download the updated history file. **This is the only copy**, so keep it somewhere safe. It contains names and emails, so never share it or post it anywhere public.
   You can also download a **mailing list**: one column of every unique email address in the history.

### Which Zoom file

Any of these works:

- the **Participants report** (best: has real join and leave times)
- the **Usage report**
- the **Meeting list details** file

In Zoom: Reports → Usage → pick the meeting → Participants → Export.

### One person at a time

Only one person should add sessions to a history file at a time. If two people each add new people to their own copies, the same ID can be given to two different people. Whoever adds the sessions should share the newest history file with the next person.

## The history file

One Excel file holds everything:

| Sheet | What it holds |
| --- | --- |
| `settings` | department name and overview title |
| `meeting_types` | each type, and whether it has topics, presenters, its usual length, and whether it is hidden |
| `people_db` | everyone who has attended, with department and status |
| `dept_to_parent` | which college or unit each department belongs to |
| `name_aliases` | Zoom names that have been matched to a person |
| `sessions` | topic, presenters and meeting length for each session |
| `attendance_log` | who attended which session, and for how long |

You can open it in Excel to fix a department or status. Keep the sheet and column names as they are. History files from the earlier experiment page also load.

## For maintainers

The page is built from these files. **Edit them, never `index.html`**, which is generated.