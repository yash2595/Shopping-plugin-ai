from pydantic import BaseModel, ConfigDict


class BaseSchema(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        from_attributes=True,
    )


class ErrorDetail(BaseSchema):
    code: str
    message: str


class ErrorResponse(BaseSchema):
    error: ErrorDetail
