"""The TV gateway: the separate, paired-device door into Friday.

Design: ftv/program/specs/tv_desktop_pairing_SLICE_PLAN_2026-11-02.md.

It is not the main app and shares none of its trust:

  * its own Flask app and listener, never port 3000, TLS 1.3 only;
  * no loopback trust: every route but the three session-opening ones needs
    a device session, whatever address the request came from;
  * it never accepts the main API token or the session cookie, and the main
    app never accepts a device session (one test each way);
  * it serves no page, no API token, no desk key;
  * off until the owner turns it on at the PC, and then bound to one LAN
    address, never 0.0.0.0.
"""
