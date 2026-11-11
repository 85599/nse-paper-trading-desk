name: Wrong refusal or thin pass
description: A room refused something it should not have, or passed something it should not have
labels: ["correctness"]
body:
  - type: markdown
    attributes:
      value: The most useful reports are a room that refused for the wrong reason, or accepted on a thin one.
  - type: input
    id: room
    attributes:
      label: Room id
      description: the folder under workspace/team/ (or the output of `desk.py team-report`)
      placeholder: idfcfirstb-20261109-153951
    validations:
      required: true
  - type: textarea
    id: command
    attributes:
      label: The command you ran
      render: shell
      placeholder: python scripts/desk.py team-review RELIANCE
    validations:
      required: true
  - type: textarea
    id: conclusion
    attributes:
      label: The CONCLUSION line and the verdicts behind it
      render: text
      description: paste the room conclusion, plus the specialist rows that look wrong
    validations:
      required: true
  - type: textarea
    id: expected
    attributes:
      label: What you expected instead, and why
      description: which artifact, rule or date should have decided it
    validations:
      required: true
