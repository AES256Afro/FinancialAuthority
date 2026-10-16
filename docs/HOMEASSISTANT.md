# Home Assistant and widgets

FinancialAuthority gives a dashboard the few figures worth a glance: safe to spend, days to payday, the next bill,
how many things wait for you, and whether the plan is on track. Home Assistant reads them over the network with an
API key; an iPhone widget reads the same through a Shortcut.

Nothing here can change anything: the key is read-only.

## 1. Make a key (in the app)

1. Open FinancialAuthority, then **Settings › Apps and keys**.
2. Under **Keys for scripts**, name it `Home Assistant`, tick **Read** only, and choose what it can see:
   - **Everything**: the next bill's name and the one-line sentence show on the dashboard.
   - **Totals only**: amounts and dates, never a name. Choose this if other people see the dashboard.
3. Press **Make a key** and copy the key it shows once. It starts with `fa_`.

## 2. Tell Home Assistant (on the machine that runs Home Assistant)

Open Home Assistant's `secrets.yaml` (in the config folder, the same place as `configuration.yaml`) and add:

```yaml
financialauthority_key: "Bearer fa_paste_the_key_here"
```

Then add this to `configuration.yaml`. Replace the address with your app's: the tailnet address and port when Home
Assistant is on the same tailnet (for example `http://your-server:8430`), or the https address when the app is published.

```yaml
rest:
  - resource: http://your-server:8430/api/v1/ha
    scan_interval: 900
    headers:
      Authorization: !secret financialauthority_key
    sensor:
      - name: Safe to spend
        value_template: "{{ value_json.safe_to_spend }}"
        unit_of_measurement: "$"
        device_class: monetary
      - name: Days to payday
        value_template: "{{ value_json.days_to_payday }}"
        unit_of_measurement: "days"
      - name: Next bill
        value_template: "{{ value_json.next_bill_name }}"
        json_attributes:
          - next_bill_amount
          - next_bill_date
          - next_bill_days
      - name: Money needs you
        value_template: "{{ value_json.needs_you }}"
      - name: Money plan
        value_template: "{{ value_json.status_word }}"
        json_attributes:
          - sentence
          - safe_through
          - payday
          - payday_amount
```

Restart Home Assistant (**Settings › System › Restart**). The five sensors appear under **Settings › Devices &
services › Entities**; search for "Safe to spend".

## 3. A dashboard card

On a dashboard, **Edit dashboard › Add card › Manual**, and paste:

```yaml
type: entities
title: Money
entities:
  - entity: sensor.safe_to_spend
  - entity: sensor.days_to_payday
  - entity: sensor.next_bill
    secondary_info: attribute
  - entity: sensor.money_needs_you
  - entity: sensor.money_plan
```

## 4. The iPhone widget

The same figures come from `/api/v1/glance`, which also carries a sentence to speak.

1. On the iPhone, open **Shortcuts**, tap **+**, and add **Get Contents of URL**.
2. URL: your app's address followed by `/api/v1/glance`. Tap **Show More**, add a header `Authorization` with the
   value `Bearer ` and your key.
3. Add **Get Dictionary Value**: `text`. Then **Speak Text** or **Show Result**.
4. Name it `Money`. Add it to the Home Screen as a widget (long-press the Home Screen, **+**, **Shortcuts**).
   "Hey Siri, Money" reads the sentence aloud.

If a sensor shows `unavailable`, open the address from step 2 in a browser on the same network: a `401` means the
key was mistyped or revoked (make a new one); no answer means the address or port is wrong.
