# Prolific launch setup

## 1. External study URL

Use the deployed task URL with Prolific's participant, study, and submission placeholders:

```text
https://crowdsource-code-platform-production.up.railway.app/?PROLIFIC_PID={{%PROLIFIC_PID%}}&STUDY_ID={{%STUDY_ID%}}&SESSION_ID={{%SESSION_ID%}}
```

The task stores these identifiers in the session manifest alongside the programming trace. It does not display the participant ID in the interface.

When the task is opened from an external survey without URL parameters, it requires the participant to enter their Prolific ID before the editor is enabled. Direct development previews can bypass that prompt with `?preview=1`.

## 2. Completion path

In the Prolific study setup:

1. Add a successful completion path.
2. Choose automatic redirect by URL.
3. Copy the generated URL. It should use the `https://app.prolific.com/submissions/complete` host and include the study completion code.
4. Set that complete URL as the Railway environment variable `PROLIFIC_COMPLETION_URL`.
5. Redeploy the Railway service.

The server only returns this URL after it recompiles the submitted source, all five semantic checks pass, and the submission is saved. Other completion hosts are rejected.

## 3. Pilot checklist

- Open the study through Prolific Preview and confirm the header says `Prolific session`.
- Submit the starter code and confirm it is rejected with five visible test results.
- Complete the task and confirm all five tests pass.
- Click Submit and confirm the accepted dialog appears.
- Click Return to Prolific and confirm the correct completion code is registered.
- Confirm the matching trace manifest contains `recruitment.prolific_pid`, `study_id`, and `prolific_session_id`.
- Confirm `delta-observations/step_labels.jsonl` covers the expected code snapshots, documentation views, and Run/Submit markers.
- Confirm the session contains a passing JSON file under `submissions/`.
- Run a small paid pilot before opening all study places.

## 4. Study task

Participants complete a KnitScript program that:

- uses exactly front-bed needles 0 through 9;
- casts on all 10 stitches with tuck operations;
- knits two complete securing rows before `releasehook`;
- knits exactly six complete rows after `releasehook`;
- alternates direction across those six rows.

The checker evaluates generated knitout rather than comparing participant source code with a reference answer.
