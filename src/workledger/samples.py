from sqlalchemy import select

from .models import Audit, Camera, Company, Employee, SourceEvent


def archive_generated_lists(factory, store=None):
    """Hide only known generated records; preserve configured/enrolled/observed records."""
    names = [
        "Ayesha Rahman",
        "Tanvir Hasan",
        "Nusrat Jahan",
        "Arif Ahmed",
        "Farhana Islam",
        "Rafiul Karim",
        "Mehedi Rahman",
        "Sadika Noor",
        "Imran Hossain",
        "Sadia Akter",
        "Fahim Chowdhury",
        "Maliha Khan",
        "Rakib Alam",
        "Nabila Sultana",
        "Samiul Haque",
    ]
    camera_names = {"Main entrance", "Office exit"} | {
        f"Workspace {number:02}" for number in range(1, 11)
    }
    with factory() as db:
        for company in db.scalars(select(Company).where(Company.demo.is_(True))).all():
            enrolled = {t["employee_id"] for t in store.templates(company.id)} if store else set()
            real_ids = set(
                db.scalars(
                    select(SourceEvent.employee_id).where(
                        SourceEvent.company_id == company.id, SourceEvent.simulated.is_(False)
                    )
                ).all()
            )
            for employee in db.scalars(
                select(Employee).where(Employee.company_id == company.id)
            ).all():
                expected = {f"EMP-{number:03}": name for number, name in enumerate(names, 1)}
                if (
                    expected.get(employee.code) == employee.name
                    and employee.id not in enrolled
                    and employee.id not in real_ids
                ):
                    employee.sample = True
                    previous_code = employee.code
                    # Release the employee ID for the real team without deleting
                    # fixture history or reassigning its event foreign keys.
                    employee.code = f"SAMPLE-{previous_code}-{employee.id[:8]}"
                    db.add(
                        Audit(
                            company_id=company.id,
                            actor_id="system",
                            action="sample.archived",
                            target=employee.id,
                            detail={"original_code": previous_code, "archived_code": employee.code},
                        )
                    )
            for camera in db.scalars(select(Camera).where(Camera.company_id == company.id)).all():
                real = db.scalar(
                    select(SourceEvent.id)
                    .where(SourceEvent.camera_id == camera.id, SourceEvent.simulated.is_(False))
                    .limit(1)
                )
                if (
                    camera.name in camera_names
                    and not real
                    and not (store and store.camera(camera.id))
                ):
                    camera.sample = True
            company.demo = False
        db.commit()
