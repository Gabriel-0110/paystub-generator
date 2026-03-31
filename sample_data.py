"""
Sample employee configuration used by main.py for demo / development.

The runtime config is now composed from reusable stored profiles rather than
being hardcoded inline.
"""
from models.profile_store import load_employee_pay_config


sample_employee = load_employee_pay_config(
    company_profile_id="northwind_ops_llc",
    employee_profile_id="sample_employee",
    tax_profile_id="ny_single_biweekly",
    deduction_profile_id="default_employee_deductions",
    payroll_check_number="000000001",
)
