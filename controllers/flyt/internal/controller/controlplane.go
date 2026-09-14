package controller

import (
    "context"
    "net"

    api "github.com/WoogiBoogi1129/flyt-k8s-poc/controllers/flyt/api/v1alpha1"
    corev1 "k8s.io/api/core/v1"
    "k8s.io/apimachinery/pkg/types"
    ctrl "sigs.k8s.io/controller-runtime"
    "sigs.k8s.io/controller-runtime/pkg/client"
)
type PlaneReconciler struct{Base}
func (r *PlaneReconciler) Reconcile(ctx context.Context,req ctrl.Request)(ctrl.Result,error){
    p:=&api.FlytControlPlane{};if err:=r.Reader.Get(ctx,req.NamespacedName,p);err!=nil{return ctrl.Result{},client.IgnoreNotFound(err)}
    workers,err:=r.workers(ctx,p.Namespace);if err!=nil{return ctrl.Result{},err}
    count:=int32(0);valid:=map[string]bool{}
    for _,w:=range workers{if w.Spec.ControlPlaneRef.Name==p.Name{count++};if w.Spec.ControlPlaneRef.UID==string(p.UID){valid[string(w.UID)]=true}}
    if !p.DeletionTimestamp.IsZero(){
        if count>0{return r.mark(ctx,p,"Terminating","InUse","Control plane retained until referencing FlytWorkers complete cleanup")}
        done,err:=r.stopResources(ctx,p,"manager");if err!=nil{return ctrl.Result{},err};if !done{return r.mark(ctx,p,"Terminating","WaitingForPods","Waiting for manager Pod termination")}
        _,err=r.finalizer(ctx,p,false);return ctrl.Result{},err
    }
    if changed,err:=r.finalizer(ctx,p,true);changed||err!=nil{return ctrl.Result{Requeue:true},err}
    for _,o:=range planeResources(p){if err=r.ensure(ctx,p,o);err!=nil{return r.mark(ctx,p,"Blocked","ApplyFailed",err.Error())}}
    response,err:=r.binding(ctx,p,map[string]interface{}{"op":"list"});if err!=nil{return r.mark(ctx,p,"Pending","ManagerUnavailable",err.Error())}
    // Rebuild membership from Kubernetes after controller restarts, including
    // cleanup of bindings whose CR was removed outside normal finalization.
    for _,binding:=range response.Bindings{if !valid[binding.WorkerUID]{
        if _,err=r.binding(ctx,p,map[string]interface{}{"op":"unbind","worker_uid":binding.WorkerUID});err!=nil{return ctrl.Result{},err}
    }}
    service:=&corev1.Service{};if err=r.Reader.Get(ctx,types.NamespacedName{Namespace:p.Namespace,Name:planeName(p)},service);err!=nil{return ctrl.Result{},err}
    err=r.status(ctx,p,"Ready","ManagerAvailable","Binding API available; membership changes do not restart the manager",func(s *api.Status){
        s.Consumers=count;s.Endpoint=net.JoinHostPort(service.Spec.ClusterIP,"12402");s.ManagerEpoch=response.Epoch})
    return ctrl.Result{RequeueAfter:Period},err
}
