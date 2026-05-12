# DEPRECATED — no longer imported by plan/validate.py
#
# plan_allow was removed from /plan/validate because it created a second
# independent OPA decision path alongside Gateway's authz.allow, with a
# different input schema and different glob separators, which could produce
# contradictory decisions on the same request.
#
# OPA remains the authoritative PDP; Gateway enforces authz.allow at
# execution time.  This file is kept for reference only and will be removed
# in a future cleanup commit.
