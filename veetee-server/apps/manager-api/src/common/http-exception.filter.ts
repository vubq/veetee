import {
  ArgumentsHost,
  Catch,
  type ExceptionFilter,
  HttpException,
  HttpStatus,
} from "@nestjs/common";
import type { FastifyReply, FastifyRequest } from "fastify";

@Catch()
export class HttpExceptionFilter implements ExceptionFilter {
  catch(exception: unknown, host: ArgumentsHost): void {
    const response = host.switchToHttp().getResponse<FastifyReply>();
    const request = host.switchToHttp().getRequest<FastifyRequest>();
    const status = exception instanceof HttpException ? exception.getStatus() : HttpStatus.INTERNAL_SERVER_ERROR;
    const payload = exception instanceof HttpException ? exception.getResponse() : undefined;
    const details = typeof payload === "object" && payload ? payload : undefined;
    const rawMessage =
      typeof payload === "string"
        ? payload
        : details && "message" in details
          ? details.message
          : undefined;
    const message = Array.isArray(rawMessage)
      ? "Request validation failed"
      : typeof rawMessage === "string"
        ? rawMessage
        : status >= 500
          ? "Internal server error"
          : "Request failed";
    response.status(status).send({
      code: this.codeFor(status),
      message,
      ...(Array.isArray(rawMessage) ? { details: rawMessage } : {}),
      request_id: request.id,
    });
  }

  private codeFor(status: number): string {
    const names: Record<number, string> = {
      400: "bad_request",
      401: "unauthorized",
      403: "forbidden",
      404: "not_found",
      409: "conflict",
      422: "unprocessable_entity",
      429: "rate_limited",
      503: "service_unavailable",
    };
    return names[status] ?? (status >= 500 ? "internal_error" : "request_failed");
  }
}
