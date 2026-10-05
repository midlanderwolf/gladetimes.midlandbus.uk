from copy import copy, deepcopy


class LockedOperatorAdminMixin:
    """Blocks change/delete permission in the Django admin for records belonging
    to a locked Operator (busstops.Operator.locked), unless the user is a superuser.

    `locked_operator_field` is the (possibly double-underscore separated) path from
    the model instance to the relevant Operator - an empty string means the model
    itself *is* the Operator.
    """

    locked_operator_field = "operator"

    def _get_locked_operator(self, obj):
        operator = obj
        if self.locked_operator_field:
            for part in self.locked_operator_field.split("__"):
                if operator is None:
                    return None
                operator = getattr(operator, part)
        return operator

    def _blocked_by_operator_lock(self, request, obj):
        if obj is None or request.user.is_superuser:
            return False
        operator = self._get_locked_operator(obj)
        return bool(operator and operator.locked)

    def has_change_permission(self, request, obj=None):
        if self._blocked_by_operator_lock(request, obj):
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if self._blocked_by_operator_lock(request, obj):
            return False
        return super().has_delete_permission(request, obj)


class M2MThroughMixin:
    """ManyToManyField with an explicit through model (to support DB_CASCADE):
    where there are no extra fields, pretend the through model was auto-created,
    so the field is editable in the Django admin panel."""

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        through = db_field.remote_field.through
        if not through._meta.auto_created and all(
            field.is_relation or field.primary_key for field in through._meta.fields
        ):
            meta = copy(through._meta)
            meta.auto_created = through
            db_field = deepcopy(db_field)
            db_field.remote_field.through = type(through.__name__, (), {"_meta": meta})
        return super().formfield_for_manytomany(db_field, request, **kwargs)
